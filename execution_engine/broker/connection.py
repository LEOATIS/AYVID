"""Interactive Brokers connection management via ib_async.

Handles:
- Async connection establishment
- Thread isolation (THE ONE RULE: never block the IB event loop)
- Daily 23:45 ET reset recovery with exponential backoff
"""

from __future__ import annotations

import asyncio
from typing import Any

from shared.config import settings
from shared.exceptions import IBKRDisconnectError
from shared.logger import get_logger

log = get_logger("broker.conn")

_ib = None
_connected = False
_last_client_id_collision = False
# Single-flight reconnect: P0-G guard. Populated only when a recovery
# coroutine is in flight; cleared by `_clear_recovery_task` when that
# coroutine terminates (success OR failure). Subsequent disconnect
# events during this window are ignored so we never schedule parallel
# `full_recovery_sequence` runs racing on `discard` of orphans.
_recovery_task: asyncio.Task | None = None


async def connect(connect_timeout: float = 8.0, max_client_id_retries: int = 10) -> Any:
    """Establish connection to IB Gateway / TWS.

    Adds two pieces of operational hardening on top of ib_async.connectAsync:

    1. ``connect_timeout`` — bound the whole handshake so we surface a
       clear error instead of silently hanging when Gateway is in a
       degraded state.
    2. Auto-bump the clientId on collision (IB Error 326). After a hard
       crash IB Gateway can take 30–60s to release the previous clientId,
       and a stuck reconnect loop is the #1 paper-trading footgun. We
       retry up to ``max_client_id_retries`` times with clientId+1 each
       round, so the runner just keeps moving.
    """
    global _ib, _connected, _last_client_id_collision

    from ib_async import IB

    base_client_id = settings.IB_CLIENT_ID
    last_exc: Exception | None = None

    for attempt in range(max_client_id_retries):
        client_id = base_client_id + attempt
        _last_client_id_collision = False
        try:
            _ib = IB()
            _ib.errorEvent += _on_error  # attach early so we capture err 326
            await asyncio.wait_for(
                _ib.connectAsync(
                    host=settings.IB_HOST,
                    port=settings.IB_PORT,
                    clientId=client_id,
                    readonly=False,
                    timeout=connect_timeout,
                ),
                timeout=connect_timeout + 2.0,
            )
            _connected = True
            _ib.disconnectedEvent += _on_disconnect

            account = (settings.IB_ACCOUNT or "").strip()
            # IB auto-subscribes account updates for the primary account on
            # connect. Calling reqAccountUpdatesAsync(account) again for
            # the *same* primary account hangs forever — IB never sends
            # the second accountDownloadEnd callback. Only call it if the
            # caller has explicitly opted into a non-primary sub-account.
            if account and settings.IB_SUBSCRIBE_ACCOUNT_UPDATES:
                try:
                    await asyncio.wait_for(_ib.reqAccountUpdatesAsync(account), timeout=3.0)
                except TimeoutError:
                    log.warning(
                        f"reqAccountUpdatesAsync({account}) timed out — "
                        f"continuing with auto-subscribed primary account"
                    )

            log.info(
                f"Connected to IB Gateway at {settings.IB_HOST}:{settings.IB_PORT} "
                f"(clientId={client_id}, account={account or 'default'})"
            )

            # Wait briefly for ib_async to populate the local account-values
            # cache so the first poll cycle has real data, not zeros.
            try:
                await asyncio.wait_for(_wait_for_first_account_value(_ib), timeout=5.0)
            except TimeoutError:
                log.warning(
                    "IB account values not populated within 5s — first cycle "
                    "may report 0. Check that the account is funded and "
                    "data subscriptions are active."
                )
            return _ib

        except (TimeoutError, Exception) as exc:
            last_exc = exc
            _safe_disconnect()
            msg = str(exc).lower()
            collision = (
                _last_client_id_collision or "326" in msg or "client id is already in use" in msg
            )
            if collision:
                log.warning(
                    f"clientId={client_id} already in use; retrying with clientId={client_id + 1}"
                )
                await asyncio.sleep(1.0)
                continue
            if isinstance(exc, asyncio.TimeoutError):
                log.error(
                    f"IB connectAsync timed out after {connect_timeout}s "
                    f"(clientId={client_id}). Gateway may be unresponsive — "
                    f"try `lsof -nP -iTCP:{settings.IB_PORT}` and kill orphans."
                )
            else:
                log.error(f"IB connection failed (clientId={client_id}): {exc}")
            break

    _connected = False
    raise IBKRDisconnectError(f"Cannot connect to IB: {last_exc}") from last_exc


def _safe_disconnect() -> None:
    """Best-effort disconnect that never raises."""
    global _ib
    try:
        if _ib is not None and _ib.isConnected():
            _ib.disconnect()
    except Exception:
        pass


async def disconnect() -> None:
    """Cleanly disconnect from IB.

    Detaches the disconnectedEvent handler first so we don't trigger the
    auto-recovery loop when the disconnect was intentional (Ctrl-C, end
    of a script, etc.).
    """
    global _ib, _connected
    if _ib:
        try:
            _ib.disconnectedEvent -= _on_disconnect
        except Exception:
            pass
        _ib.disconnect()
        _connected = False
        log.info("Disconnected from IB Gateway")


def is_connected() -> bool:
    """Check if we have an active IB connection."""
    return _connected and _ib is not None and _ib.isConnected()


async def reconnect_with_backoff(max_retries: int = 10) -> Any:
    """Exponential backoff reconnection for the 23:45 ET daily reset.

    Preserves client ID to recover session state on the server side.
    """
    global _ib, _connected
    base_delay = 5.0

    for attempt in range(1, max_retries + 1):
        delay = min(base_delay * (2 ** (attempt - 1)), 300.0)
        log.warning(f"Reconnection attempt {attempt}/{max_retries} in {delay:.0f}s...")
        await asyncio.sleep(delay)

        try:
            ib = await connect()
            from execution_engine.broker.recovery import full_recovery_sequence

            recovered = await full_recovery_sequence(ib)
            log.info(
                f"Full recovery complete: {len(recovered)} positions reconciled, "
                f"bracket orders validated"
            )
            return ib
        except Exception as exc:
            log.warning(f"Attempt {attempt} failed: {exc}")

    raise IBKRDisconnectError(f"Failed to reconnect after {max_retries} attempts")


async def _wait_for_first_account_value(ib: Any) -> None:
    """Block until ib_async has cached at least one account value."""
    while not ib.accountValues():
        await asyncio.sleep(0.1)


def _clear_recovery_task(task: asyncio.Task) -> None:
    """Done-callback for the single-flight recovery task.

    Fires when ``reconnect_with_backoff`` terminates (returns or
    raises). Always clears the module-level reference so the next
    legitimate disconnect can spawn a fresh recovery run. Surfaces
    the failure at WARNING so operators see it without the callback
    itself raising.
    """
    global _recovery_task
    _recovery_task = None
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        log.warning("IB recovery task was cancelled")
        return
    if exc is not None:
        log.warning(f"IB recovery task ended with error: {exc}")


def _on_disconnect() -> None:
    """Handle IB disconnect event (Error 1100).

    P0-G single-flight guard: if a recovery task is already in flight
    we skip. Without this guard a flapping connection scheduled an
    unbounded fan-out of ``reconnect_with_backoff`` tasks, each
    running its own ``full_recovery_sequence`` in parallel — racing
    on bracket orphan discovery, order cancellation, and clientId
    reassignment.
    """
    global _connected, _recovery_task
    _connected = False

    if _recovery_task is not None and not _recovery_task.done():
        log.info(
            "IB Gateway disconnected — recovery already in flight "
            f"(task={_recovery_task.get_name()}), suppressing duplicate"
        )
        return

    log.warning("IB Gateway disconnected — initiating recovery")
    try:
        _recovery_task = asyncio.create_task(
            reconnect_with_backoff(),
            name="ibkr_recovery",
        )
    except RuntimeError as exc:
        # No running event loop (e.g. callback fired on a non-asyncio
        # thread during teardown). Nothing we can do from here.
        log.error(f"Cannot schedule IB recovery — no running loop: {exc}")
        _recovery_task = None
        return
    _recovery_task.add_done_callback(_clear_recovery_task)


def _on_error(reqId: int, errorCode: int, errorString: str, contract: Any) -> None:
    """Handle IB error events."""
    global _last_client_id_collision
    if errorCode == 326:
        _last_client_id_collision = True
        log.warning(f"IB Error 326: {errorString}")
    elif errorCode == 1100:
        log.error("Error 1100: Connectivity lost")
        _on_disconnect()
    elif errorCode == 1102:
        log.info("Error 1102: Connectivity restored")
    elif errorCode in (2103, 2104, 2106, 2107, 2108, 2158):
        log.debug(f"IB Info [{errorCode}]: {errorString}")
    else:
        log.warning(f"IB Error [{errorCode}] req={reqId}: {errorString}")


async def get_ib() -> Any:
    """Get the IB client, connecting if necessary."""
    if not is_connected():
        return await connect()
    return _ib
