#!/usr/bin/env bash
###############################################################################
# AYVID — roll the execution VM container to a new image tag.
#
# Runs ON the VM (executed via `gcloud compute ssh ... --command`).
# Fetches a fresh metadata-server access token, re-pulls the latest
# image at the requested tag, and restarts the `ayvid-execution`
# container with the same env-file the boot script wrote.
#
# Usage (typically from .github/workflows/deploy-gcp.yml):
#   ssh "bash -s" < scripts/vm_redeploy.sh <region> <project> <tag>
###############################################################################

set -euo pipefail

REGION="${1:?region required}"
PROJECT="${2:?project required}"
TAG="${3:?image tag required}"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/ayvid/ayvid-execution:${TAG}"
DOCKER_CONFIG_DIR=/var/lib/docker-config
SECRETS_FILE=/var/run/ayvid.env

# Persisted host paths — bind-mounted into the container so live/* and
# data_store/* survive every container restart.
HOST_LIVE_DIR=/var/lib/ayvid/live
HOST_DATA_DIR=/var/lib/ayvid/data_store
# Mirror these to GCS so a fresh VM can hydrate from the cloud copy
# rather than starting at 0 snapshots / empty signal book.
GCS_BUCKET="${PROJECT}-tfstate"
GCS_PREFIX="live_state"

echo "[vm_redeploy] target image: ${IMAGE}"

# Refresh docker auth — the token from boot may have expired.
ACCESS_TOKEN=$(curl -sf -H "Metadata-Flavor: Google" \
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token" \
    | tr -d '\n' \
    | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
[ -n "$ACCESS_TOKEN" ] || { echo "FATAL: no metadata token" >&2; exit 1; }

sudo mkdir -p "$DOCKER_CONFIG_DIR"
sudo chmod 700 "$DOCKER_CONFIG_DIR"
echo "$ACCESS_TOKEN" | sudo docker --config "$DOCKER_CONFIG_DIR" login \
    -u oauth2accesstoken --password-stdin "${REGION}-docker.pkg.dev"

echo "[vm_redeploy] pulling image..."
sudo docker --config "$DOCKER_CONFIG_DIR" pull "$IMAGE"

# Refresh secrets file in case any rotated since boot.
#
# History: the prior implementation parsed the Secret Manager JSON
# response with sed regexes assuming compact JSON ("name":"…"). The
# real API returns pretty-printed JSON ("name": "…") with whitespace
# after every colon, so the regex matched zero secrets — every cycle
# the runner booted with no API keys, ALL LLM calls 401'd, and the
# pipeline minted 0 signals from real news. (May 3 incident, after
# the OpenBB chown fix unmasked the LLM auth failure.) Switched to
# python3 for both the listing and the per-secret access response —
# any whitespace / paginated / re-ordered response shape works as
# long as the JSON is valid. python3 is already used elsewhere in
# this script (URL-encoding) so no new dep on COS.
#
# Requires the VM service account to have BOTH
# roles/secretmanager.secretAccessor (read values) AND
# roles/secretmanager.viewer (list — secrets.list permission). The
# accessor role alone returns 403 on the `secrets` list endpoint.
echo "[vm_redeploy] refreshing secrets file..."
sudo bash -c ": > $SECRETS_FILE"
SECRET_IDS=$(curl -sf -H "Authorization: Bearer $ACCESS_TOKEN" \
    "https://secretmanager.googleapis.com/v1/projects/${PROJECT}/secrets?pageSize=200" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(n["name"].split("/")[-1]) for n in d.get("secrets",[]) if n["name"].split("/")[-1].startswith("ayvid-")]')
SECRET_COUNT=$(echo "$SECRET_IDS" | grep -c .)
echo "[vm_redeploy] discovered ${SECRET_COUNT} ayvid-* secrets"
SECRET_OK=0; SECRET_FAIL=0
for secret_id in $SECRET_IDS; do
    env_name=$(echo "$secret_id" | sed 's/^ayvid-//' | tr '[:lower:]-' '[:upper:]_')
    encoded=$(curl -sf -H "Authorization: Bearer $ACCESS_TOKEN" \
        "https://secretmanager.googleapis.com/v1/projects/${PROJECT}/secrets/${secret_id}/versions/latest:access" \
        | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("payload",{}).get("data",""))')
    if [ -z "$encoded" ]; then SECRET_FAIL=$((SECRET_FAIL+1)); continue; fi
    value=$(echo "$encoded" | base64 -d)
    printf '%s=%s\n' "$env_name" "$value" | sudo tee -a "$SECRETS_FILE" >/dev/null
    SECRET_OK=$((SECRET_OK+1))
done
echo "[vm_redeploy] secrets written ok=${SECRET_OK} failed=${SECRET_FAIL}"

# Plaintext config env vars. compute.tf writes these on FIRST boot only;
# without re-injecting them on every redeploy, vm_redeploy.sh's
# `: > $SECRETS_FILE` truncate above wipes them and the runner loses
# AYVID_LIVE_GCS_BUCKET / EXECUTION_MODE / etc. The May 3 incident was
# this exact bug: the marker write succeeded locally on the VM but
# `_sync_path_to_gcs` returned early because $AYVID_LIVE_GCS_BUCKET
# was empty post-redeploy, so Cloud Run could never see the marker
# and the smoke test looped 12× on `runner_sha=unknown`.
# Source-of-truth values match compute.tf exactly.
{
  printf 'AYVID_LIVE_GCS_BUCKET=%s\n'      "${PROJECT}-tfstate"
  printf 'AYVID_LIVE_GCS_PREFIX=%s\n'      "live_state"
  printf 'EXECUTION_MODE=%s\n'             "ib_paper"
  printf 'AUTO_EXECUTE=%s\n'               "true"
  printf 'LIVE_TRADING_CONFIRMED=%s\n'     "false"
  printf 'CONTINUOUS_PIPELINE_ENABLED=%s\n' "true"
  printf 'LLM_ORCHESTRATION_ENABLED=%s\n'  "true"
  printf 'LLM_SIGNAL_TIMEOUT_SECONDS=%s\n' "45"
  printf 'LLM_MAX_CONCURRENT_SIGNALS=%s\n' "5"
} | sudo tee -a "$SECRETS_FILE" >/dev/null

sudo chmod 600 "$SECRETS_FILE"

# ── HYDRATE LIVE DATA FROM GCS ────────────────────────────────────────
# Without this, every redeploy resets equity_curve.jsonl /
# trades.jsonl / positions.json to whatever the VM's persisted disk
# holds. If the VM was rebuilt (terraform apply on compute.tf, machine
# replacement) those files are GONE → console drops to "20 snapshots
# 2d ago" symptom. Pull from the GCS mirror first; the runner will
# atomically overwrite + re-mirror them every cycle.
echo "[vm_redeploy] hydrating live/ + data_store/ from gs://${GCS_BUCKET}/${GCS_PREFIX}/ ..."
sudo mkdir -p "$HOST_LIVE_DIR" "$HOST_DATA_DIR"
sudo chmod 755 "$HOST_LIVE_DIR" "$HOST_DATA_DIR"
# CRITICAL: the container runs as uid 1001 (`ayvid` user — see
# Dockerfile.execution). Bind-mounted host dirs preserve the host's
# owner, so unless we chown to 1001 here the runner gets EACCES on
# every write to live/ and crashes in a Docker restart-loop on boot
# (deploy_marker.tmp / run_meta.json fail). The May 3 incident was
# this exact bug.
sudo chown -R 1001:1001 "$HOST_LIVE_DIR" "$HOST_DATA_DIR"

# COS doesn't ship gsutil; use the same metadata-token pattern via
# curl + the GCS JSON API. Robust against gsutil absence.
hydrate_from_gcs() {
    local rel_prefix="$1"   # e.g. "live"   or "data_store"
    local dest_dir="$2"     # absolute host path
    local list_url="https://storage.googleapis.com/storage/v1/b/${GCS_BUCKET}/o?prefix=${GCS_PREFIX}/${rel_prefix}/"
    local objects
    objects=$(curl -sf -H "Authorization: Bearer ${ACCESS_TOKEN}" "$list_url" 2>/dev/null \
        | sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' || true)
    if [ -z "$objects" ]; then
        echo "[vm_redeploy] gcs hydrate: no objects under ${GCS_PREFIX}/${rel_prefix}/ (cold start)"
        return 0
    fi
    while IFS= read -r obj; do
        [ -z "$obj" ] && continue
        # Skip "directory" placeholder objects ending in /
        case "$obj" in */) continue ;; esac
        # local path = dest_dir + (obj minus prefix)
        local rel="${obj#${GCS_PREFIX}/${rel_prefix}/}"
        local out="${dest_dir}/${rel}"
        sudo mkdir -p "$(dirname "$out")"
        # URL-encode '/' in object name for the media GET
        local encoded
        encoded=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=""))' "$obj")
        local media_url="https://storage.googleapis.com/storage/v1/b/${GCS_BUCKET}/o/${encoded}?alt=media"
        curl -sf -H "Authorization: Bearer ${ACCESS_TOKEN}" "$media_url" \
            | sudo tee "$out" >/dev/null && echo "[vm_redeploy] hydrated ${rel}"
    done <<< "$objects"
}
hydrate_from_gcs "live"       "$HOST_LIVE_DIR"       || echo "[vm_redeploy] live hydrate failed (non-fatal)"
hydrate_from_gcs "data_store" "$HOST_DATA_DIR"       || echo "[vm_redeploy] data_store hydrate failed (non-fatal)"

# Seed reference data baked into the image. The runner reads bars from
# data_store/{ohlcv,intraday} but never writes them — they're static
# reference data shipped via `COPY . .` in Dockerfile.execution. The
# bind-mount above shadows them, and the runner falls into the cold-
# start "no_local_data" branch every cycle (paper_runner.py:919-947).
# Symptom on the May 2 incident: 22:04 UTC onward every snapshot wrote
# equity=$1M / open_positions=0 even though signals + positions existed.
# Skip subdirs that already exist so signal_book.json hydrated from GCS
# survives.
TMP_CID=$(sudo docker --config "$DOCKER_CONFIG_DIR" create "$IMAGE")
trap 'sudo docker --config "$DOCKER_CONFIG_DIR" rm "$TMP_CID" >/dev/null 2>&1 || true' EXIT
for sub in ohlcv intraday indices; do
  if [ -d "$HOST_DATA_DIR/$sub" ] && [ "$(ls -A "$HOST_DATA_DIR/$sub" 2>/dev/null | head -1)" ]; then
    echo "[vm_redeploy] data_store/$sub already populated — skipping image seed"
    continue
  fi
  if sudo docker --config "$DOCKER_CONFIG_DIR" cp "$TMP_CID:/app/data_store/$sub" "$HOST_DATA_DIR/" 2>/dev/null; then
    echo "[vm_redeploy] seeded data_store/$sub from image"
  else
    echo "[vm_redeploy] data_store/$sub not in image (skipping)"
  fi
done
sudo docker --config "$DOCKER_CONFIG_DIR" rm "$TMP_CID" >/dev/null 2>&1 || true
trap - EXIT

# `sudo tee`/`sudo mkdir`/`docker cp` above writes as root, which
# re-roots the dirs we chowned above. Final chown sweeps everything to
# 1001:1001 so the container user can read and write.
sudo chown -R 1001:1001 "$HOST_LIVE_DIR" "$HOST_DATA_DIR"

echo "[vm_redeploy] swapping container..."
sudo docker rm -f ayvid-execution 2>/dev/null || true
sudo docker run -d --restart=always --name ayvid-execution \
    --env-file "$SECRETS_FILE" \
    --cap-add=SYS_PTRACE \
    --tmpfs /tmp:exec,size=128m \
    -v "$HOST_LIVE_DIR":/app/live \
    -v "$HOST_DATA_DIR":/app/data_store \
    -p 8001:8001 \
    "$IMAGE"

echo "[vm_redeploy] container status:"
sudo docker ps --filter name=ayvid-execution --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
