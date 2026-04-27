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

echo "[vm_redeploy] swapping container..."
sudo docker rm -f ayvid-execution 2>/dev/null || true
sudo docker run -d --restart=always --name ayvid-execution \
    --env-file "$SECRETS_FILE" \
    --cap-add=SYS_PTRACE \
    --tmpfs /tmp:exec,size=128m \
    -p 8001:8001 \
    "$IMAGE"

echo "[vm_redeploy] container status:"
sudo docker ps --filter name=ayvid-execution --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
