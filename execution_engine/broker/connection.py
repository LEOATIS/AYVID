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


async def connect() -> Any:
    """Establish connection to IB Gateway / TWS.

    Returns the IB client instance.
    """
    global _ib, _connected

    try:
        from ib_async import IB
        _ib = IB()
        await _ib.connectAsync(
            host=settings.IB_HOST,
            port=settings.IB_PORT,
            clientId=settings.IB_CLIENT_ID,
            readonly=False,
        )
        _connected = True

        _ib.disconnectedEvent += _on_disconnect
        _ib.errorEvent += _on_error

        account = settings.IB_ACCOUNT or ""
        if account:
            _ib.reqAccountUpdates(account)

        log.info(
            f"Connected to IB Gateway at {settings.IB_HOST}:{settings.IB_PORT} "
            f"(client={settings.IB_CLIENT_ID})"
        )
        return _ib

    except Exception as exc:
        _connected = False
        log.error(f"IB connection failed: {exc}")
        raise IBKRDisconnectError(f"Cannot connect: {exc}") from exc


async def disconnect() -> None:
    """Cleanly disconnect from IB."""
    global _ib, _connected
    if _ib:
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
        log.warning(
            f"Reconnection attempt {attempt}/{max_retries} in {delay:.0f}s..."
        )
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

    raise IBKRDisconnectError(
        f"Failed to reconnect after {max_retries} attempts"
    )


def _on_disconnect() -> None:
    """Handle IB disconnect event (Error 1100)."""
    global _connected
    _connected = False
    log.warning("IB Gateway disconnected — initiating recovery")
    asyncio.create_task(reconnect_with_backoff())


def _on_error(reqId: int, errorCode: int, errorString: str, contract: Any) -> None:
    """Handle IB error events."""
    if errorCode == 1100:
        log.error(f"Error 1100: Connectivity lost")
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
