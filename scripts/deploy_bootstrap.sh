#!/usr/bin/env bash
###############################################################################
# AYVID — one-shot deploy bootstrap.
#
# Brings a brand-new GCP project from "billing enabled" to "Cloud Run + IBKR
# VM both running the latest images". Idempotent: every step is safe to
# re-run after a failure or a code change.
#
# Phases (matches the runbook in chat):
#   1. Enable APIs, create runtime SA + IAM, create TF state bucket
#   2. Populate Secret Manager with values from local .env / prompts
#   3. Build + push backend and execution Docker images
#   4. terraform init/plan/apply
#
# What this DOES NOT do:
#   * Phase 5 (Vercel frontend) — `cd frontend && vercel --prod` is interactive
#   * Phase 6 (api.ayvid.ai DNS) — terraform prints the records, you paste
#     them into Cloudflare; we don't touch DNS programmatically yet
#
# Usage:
#   export GCP_PROJECT=ayvid-production
#   export GCP_REGION=us-central1
#   export AYVID_INVOKER=user:likhith@ayvid.ai
#   bash scripts/deploy_bootstrap.sh [--phase 1|2|3|4|all]
###############################################################################

set -euo pipefail

GCP_PROJECT="${GCP_PROJECT:-ayvid-production}"
GCP_REGION="${GCP_REGION:-us-central1}"
GCP_ZONE="${GCP_ZONE:-us-central1-a}"
AYVID_SA_NAME="${AYVID_SA_NAME:-ayvid-runtime}"
AYVID_SA="${AYVID_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"
TFSTATE_BUCKET="${TFSTATE_BUCKET:-${GCP_PROJECT}-tfstate}"
AYVID_INVOKER="${AYVID_INVOKER:-}"   # e.g. user:founders@ayvid.ai
BACKEND_PUBLIC_INVOKER="${BACKEND_PUBLIC_INVOKER:-true}"
PHASE="${1:-all}"

# Resolve repo root regardless of where the script is invoked from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

log()  { printf "\033[1;36m[deploy]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[deploy]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[deploy]\033[0m %s\n" "$*" >&2; exit 1; }

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "missing dependency: $1"
}

require_env() {
  if [[ -z "${!1:-}" ]]; then die "env var $1 is required"; fi
}

###############################################################################
# Preflight
###############################################################################

require_cmd gcloud
require_cmd terraform
require_cmd docker
require_cmd openssl

# Ensure docker buildx is available — Mac arm64 -> linux/amd64 builds need it.
docker buildx inspect >/dev/null 2>&1 || docker buildx create --use --name ayvid >/dev/null

# Verify we're logged in.
if ! gcloud auth print-access-token >/dev/null 2>&1; then
  die "run \`gcloud auth login\` and \`gcloud auth application-default login\` first"
fi

gcloud config set project "${GCP_PROJECT}" >/dev/null
log "project=${GCP_PROJECT} region=${GCP_REGION} sa=${AYVID_SA}"

###############################################################################
# Phase 1 — APIs, service account, terraform-state bucket
###############################################################################

phase_1() {
  log "Phase 1: APIs + IAM + TF state bucket"

  gcloud services enable \
    run.googleapis.com \
    compute.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    iam.googleapis.com \
    iamcredentials.googleapis.com \
    cloudbuild.googleapis.com \
    storage.googleapis.com \
    logging.googleapis.com \
    monitoring.googleapis.com \
    --quiet

  if ! gcloud iam service-accounts describe "${AYVID_SA}" >/dev/null 2>&1; then
    log "creating service account ${AYVID_SA}"
    gcloud iam service-accounts create "${AYVID_SA_NAME}" \
      --display-name="AYVID backend + execution runtime"
  else
    log "service account ${AYVID_SA} already exists"
  fi

  for role in \
    roles/secretmanager.secretAccessor \
    roles/artifactregistry.reader \
    roles/logging.logWriter \
    roles/monitoring.metricWriter \
    roles/run.invoker; do
    gcloud projects add-iam-policy-binding "${GCP_PROJECT}" \
      --member="serviceAccount:${AYVID_SA}" \
      --role="${role}" \
      --condition=None \
      --quiet >/dev/null
  done
  log "IAM bindings applied"

  if ! gcloud storage buckets describe "gs://${TFSTATE_BUCKET}" >/dev/null 2>&1; then
    log "creating TF state bucket gs://${TFSTATE_BUCKET}"
    gcloud storage buckets create "gs://${TFSTATE_BUCKET}" \
      --location="${GCP_REGION}" \
      --uniform-bucket-level-access
    gcloud storage buckets update "gs://${TFSTATE_BUCKET}" --versioning >/dev/null
  else
    log "TF state bucket gs://${TFSTATE_BUCKET} already exists"
  fi

  # Artifact Registry repo. Terraform also defines this, but we need it
  # before phase 3 so the docker push works. Either side is idempotent
  # because the resource ID is deterministic.
  if ! gcloud artifacts repositories describe ayvid \
        --location="${GCP_REGION}" >/dev/null 2>&1; then
    log "creating Artifact Registry repo 'ayvid'"
    gcloud artifacts repositories create ayvid \
      --repository-format=docker \
      --location="${GCP_REGION}" \
      --description="AYVID images"
  else
    log "Artifact Registry repo 'ayvid' already exists"
  fi

  gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet
}

###############################################################################
# Phase 2 — Secret Manager values
#
# Reads every needed value from `.env` and pushes it to Secret Manager.
# Mapping is deterministic: env var FOO_BAR  ↔  secret id ayvid-foo-bar.
#
# Special handling:
#   * `API_KEY` is auto-generated if missing, written to .env, and
#     persisted to .deploy_state/api_key.txt so you can paste it into
#     Vercel as the frontend's API gateway token.
#   * Empty / placeholder values still produce a secret version because
#     terraform's dynamic env block requires every secret to exist —
#     but we log a warning so you can rotate them later.
###############################################################################

# Secret-id list MUST match `local.secret_ids` in infra/main.tf.
# Mapping rule: env name (UPPER_SNAKE) → secret id (lower-kebab).
ENV_TO_SECRET=(
  "OPENAI_API_KEY:openai-api-key"
  "ANTHROPIC_API_KEY:anthropic-api-key"
  "OPENBB_FMP_API_KEY:openbb-fmp-api-key"
  "OPENBB_FRED_API_KEY:openbb-fred-api-key"
  "OPENBB_BENZINGA_API_KEY:openbb-benzinga-api-key"
  "OPENBB_TIINGO_API_KEY:openbb-tiingo-api-key"
  "OPENBB_INTRINIO_API_KEY:openbb-intrinio-api-key"
  "FINNHUB_API_KEY:finnhub-api-key"
  "NEO4J_URI:neo4j-uri"
  "NEO4J_USERNAME:neo4j-username"
  "NEO4J_PASSWORD:neo4j-password"
  "QDRANT_URL:qdrant-url"
  "QDRANT_API_KEY:qdrant-api-key"
  "HOPSWORKS_API_KEY:hopsworks-api-key"
  "IB_ACCOUNT:ib-account"
  "IB_HOST:ib-host"
  "IB_PORT:ib-port"
  "PAPER_TRADING:paper-trading"
  "AUTO_EXECUTE:auto-execute"
  "IB_GATEWAY_USERNAME:ib-gateway-username"
  "IB_GATEWAY_PASSWORD:ib-gateway-password"
  "IB_GATEWAY_TRADING_MODE:ib-gateway-trading-mode"
  "IB_CLIENT_PORTAL_COOKIE:ib-client-portal-cookie"
  "API_KEY:api-key"
)

extract_env() {
  # Pulls a single value from .env without sourcing the file. Strips
  # surrounding quotes; returns empty string when the key is missing.
  # `|| true` shields the function from `set -o pipefail` when grep
  # finds no match (exit 1) — that's an expected case, not a failure.
  local key="$1"
  [[ -f .env ]] || { printf ""; return; }
  local raw
  raw="$(grep -E "^${key}=" .env 2>/dev/null | head -n 1 || true)"
  [[ -z "${raw}" ]] && { printf ""; return; }
  printf "%s" "${raw}" | cut -d= -f2- | sed 's/^"//;s/"$//' | sed "s/^'//;s/'$//"
}

ensure_api_key_in_env() {
  # API_KEY in .env is empty in the user's repo. Generate a strong one,
  # write it back so future deploys are deterministic, and also save it
  # under .deploy_state/api_key.txt so it can be copy-pasted into Vercel.
  local existing
  existing="$(extract_env API_KEY)"
  if [[ -n "${existing}" ]]; then return 0; fi

  local fresh
  fresh="$(openssl rand -hex 32)"
  log "generating new API_KEY (32 hex chars)"

  # Replace the empty `API_KEY=` line in-place. Portable across BSD/GNU sed.
  if [[ -f .env ]]; then
    if grep -qE "^API_KEY=" .env; then
      python3 -c "
import sys, re, pathlib
p = pathlib.Path('.env')
text = p.read_text()
text = re.sub(r'^API_KEY=.*\$', 'API_KEY=${fresh}', text, count=1, flags=re.MULTILINE)
p.write_text(text)
"
    else
      printf "API_KEY=%s\n" "${fresh}" >> .env
    fi
  fi

  mkdir -p .deploy_state
  printf "%s\n" "${fresh}" > .deploy_state/api_key.txt
  chmod 600 .deploy_state/api_key.txt
  log "API_KEY persisted: .deploy_state/api_key.txt (paste into Vercel as NEXT_PUBLIC_API_KEY)"
}

push_secret() {
  local id="ayvid-$1" value="$2" warn_if_empty="${3:-true}"
  if [[ -z "${value}" ]]; then
    if [[ "${warn_if_empty}" == "true" ]]; then
      warn "secret ${id}: value is empty; pushing placeholder so terraform doesn't fail"
    fi
    value="placeholder"
  fi
  if ! gcloud secrets describe "${id}" >/dev/null 2>&1; then
    printf "%s" "${value}" | gcloud secrets create "${id}" --data-file=- --replication-policy=automatic --quiet >/dev/null
    log "created secret ${id}"
  else
    printf "%s" "${value}" | gcloud secrets versions add "${id}" --data-file=- --quiet >/dev/null
    log "updated secret ${id}"
  fi
}

phase_2() {
  log "Phase 2: Secret Manager"

  if [[ ! -f .env ]]; then
    die ".env not found in $(pwd) — Phase 2 reads values from there"
  fi

  ensure_api_key_in_env

  local pair env_name secret_id value
  for pair in "${ENV_TO_SECRET[@]}"; do
    env_name="${pair%%:*}"
    secret_id="${pair##*:}"
    value="$(extract_env "${env_name}")"

    # Heuristic: values like 'your_X_here' from .env.example are NOT real.
    if [[ "${value}" =~ ^your_.*_here$ ]]; then
      warn "${env_name} looks like a placeholder ('${value}'); pushing as-is so the secret container exists"
    fi

    push_secret "${secret_id}" "${value}" "true"
  done
}

###############################################################################
# Phase 3 — build + push backend / execution images for linux/amd64
###############################################################################

phase_3() {
  log "Phase 3: build + push images"

  local registry="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT}/ayvid"
  local tag
  tag="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || date +%Y%m%d%H%M%S)"
  export AYVID_IMAGE_TAG="${tag}"
  log "tag=${tag}"

  docker buildx build --platform linux/amd64 \
    -t "${registry}/ayvid-backend:${tag}" \
    -t "${registry}/ayvid-backend:latest" \
    -f Dockerfile --push .

  docker buildx build --platform linux/amd64 \
    -t "${registry}/ayvid-execution:${tag}" \
    -t "${registry}/ayvid-execution:latest" \
    -f Dockerfile.execution --push .

  printf "%s" "${tag}" > "${REPO_ROOT}/.last_image_tag"
  log "images pushed: ayvid-backend:${tag} ayvid-execution:${tag}"
}

###############################################################################
# Phase 4 — terraform init/apply
###############################################################################

phase_4() {
  log "Phase 4: terraform"

  if [[ "${BACKEND_PUBLIC_INVOKER}" != "true" && -z "${AYVID_INVOKER}" ]]; then
    warn "AYVID_INVOKER not set and BACKEND_PUBLIC_INVOKER=false — Cloud Run will be private with NO callers."
    warn "Set AYVID_INVOKER=user:you@ayvid.ai (or group:..., serviceAccount:...) to grant invoke."
  fi

  local tag
  if [[ -f "${REPO_ROOT}/.last_image_tag" ]]; then
    tag="$(cat "${REPO_ROOT}/.last_image_tag")"
  else
    tag="latest"
    warn ".last_image_tag missing; using 'latest' (run phase 3 first for a deterministic SHA)"
  fi

  cd "${REPO_ROOT}/infra"
  terraform init -reconfigure -backend-config="bucket=${TFSTATE_BUCKET}"

  local invoker_arg=()
  if [[ -n "${AYVID_INVOKER}" ]]; then
    invoker_arg+=( -var "backend_invoker_members=[\"${AYVID_INVOKER}\"]" )
  else
    invoker_arg+=( -var 'backend_invoker_members=[]' )
  fi

  terraform apply -auto-approve \
    -var "project_id=${GCP_PROJECT}" \
    -var "region=${GCP_REGION}" \
    -var "zone=${GCP_ZONE}" \
    -var "service_account_email=${AYVID_SA}" \
    -var "image_tag=${tag}" \
    -var "backend_public_invoker=${BACKEND_PUBLIC_INVOKER}" \
    "${invoker_arg[@]}" \
    -var 'ssh_source_ranges=[]'

  log "terraform apply complete"
  log "Outputs:"
  terraform output
  cd "${REPO_ROOT}"
}

###############################################################################
# Dispatch
###############################################################################

case "${PHASE}" in
  1) phase_1 ;;
  2) phase_2 ;;
  3) phase_3 ;;
  4) phase_4 ;;
  --phase)
    case "${2:-}" in
      1) phase_1 ;;
      2) phase_2 ;;
      3) phase_3 ;;
      4) phase_4 ;;
      all|"") phase_1; phase_2; phase_3; phase_4 ;;
      *) die "unknown phase: $2" ;;
    esac
    ;;
  all|"") phase_1; phase_2; phase_3; phase_4 ;;
  *) die "unknown arg: ${PHASE} (use 1|2|3|4|all)" ;;
esac

log "done."
