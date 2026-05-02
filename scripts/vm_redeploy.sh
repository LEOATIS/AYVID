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
echo "[vm_redeploy] refreshing secrets file..."
sudo bash -c ": > $SECRETS_FILE"
for secret_id in $(curl -sf -H "Authorization: Bearer $ACCESS_TOKEN" \
    "https://secretmanager.googleapis.com/v1/projects/${PROJECT}/secrets" \
    | sed -n 's/.*"name":"projects\/[^/]*\/secrets\/\(ayvid-[^"]*\)".*/\1/p' | sort -u); do
    env_name=$(echo "$secret_id" | sed 's/^ayvid-//' | tr '[:lower:]-' '[:upper:]_')
    encoded=$(curl -sf -H "Authorization: Bearer $ACCESS_TOKEN" \
        "https://secretmanager.googleapis.com/v1/projects/${PROJECT}/secrets/${secret_id}/versions/latest:access" \
        | tr -d '\n' \
        | sed -n 's/.*"data"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    [ -z "$encoded" ] && continue
    value=$(echo "$encoded" | base64 -d)
    printf '%s=%s\n' "$env_name" "$value" | sudo tee -a "$SECRETS_FILE" >/dev/null
done
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
