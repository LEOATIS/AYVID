#!/usr/bin/env bash
###############################################################################
# AYVID — bootstrap GitHub Actions → GCP Workload Identity Federation.
#
# What this does (idempotent — safe to re-run):
#   1. Create a `github-pool` Workload Identity Pool in the GCP project.
#   2. Create a `github-provider` OIDC provider scoped to ONE GitHub repo.
#   3. Grant the EXISTING `ayvid-runtime` SA the additional roles needed
#      to ship a new revision (run.admin, compute.instanceAdmin, etc.).
#      We reuse the runtime SA on purpose — keeping a single identity
#      simplifies the YC-sprint deploy. Promote to a separate
#      `ayvid-deployer` SA later if you want stricter least-privilege.
#   4. Bind the GitHub repo principalSet to impersonate `ayvid-runtime`.
#   5. Print the values that need to go into GitHub repo secrets.
#
# After running this, set those values as GitHub repo secrets at:
#   https://github.com/LEOATIS/AYVID/settings/secrets/actions
###############################################################################

set -euo pipefail

GCP_PROJECT="${GCP_PROJECT:-ayvid-production}"
GH_REPO="${GH_REPO:-LEOATIS/AYVID}"
POOL_ID="${POOL_ID:-github-pool}"
PROVIDER_ID="${PROVIDER_ID:-github-provider}"
DEPLOYER_SA_NAME="${DEPLOYER_SA_NAME:-ayvid-runtime}"

DEPLOYER_SA="${DEPLOYER_SA_NAME}@${GCP_PROJECT}.iam.gserviceaccount.com"

log()  { printf "\033[1;36m[gha]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[gha]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[gha]\033[0m %s\n" "$*" >&2; exit 1; }

command -v gcloud >/dev/null 2>&1 || die "gcloud not found"
gcloud auth print-access-token >/dev/null 2>&1 || die "run 'gcloud auth login' first"

gcloud config set project "${GCP_PROJECT}" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "${GCP_PROJECT}" --format='value(projectNumber)')"
log "project=${GCP_PROJECT} (number=${PROJECT_NUMBER}) repo=${GH_REPO}"

# 1. Workload Identity Pool
if ! gcloud iam workload-identity-pools describe "${POOL_ID}" \
       --location="global" >/dev/null 2>&1; then
  log "creating WIF pool '${POOL_ID}'"
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --location="global" --display-name="GitHub Actions Pool"
else
  log "WIF pool '${POOL_ID}' already exists"
fi

# 2. OIDC provider scoped to one repo
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
       --location="global" \
       --workload-identity-pool="${POOL_ID}" >/dev/null 2>&1; then
  log "creating OIDC provider '${PROVIDER_ID}' (scoped to ${GH_REPO})"
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --location="global" \
    --workload-identity-pool="${POOL_ID}" \
    --display-name="GitHub provider" \
    --attribute-mapping="google.subject=assertion.sub,attribute.actor=assertion.actor,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
    --attribute-condition="assertion.repository == '${GH_REPO}'" \
    --issuer-uri="https://token.actions.githubusercontent.com"
else
  log "OIDC provider '${PROVIDER_ID}' already exists"
fi

PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"

# 3. Reuse the existing ayvid-runtime SA — refuse to proceed if missing.
if ! gcloud iam service-accounts describe "${DEPLOYER_SA}" >/dev/null 2>&1; then
  die "${DEPLOYER_SA} not found — run scripts/deploy_bootstrap.sh phase 1 first"
fi
log "reusing existing SA ${DEPLOYER_SA} for deploy duties"

# 4. Grant the additional deploy-time roles. The runtime SA already has
# secretmanager.secretAccessor + artifactregistry.reader from the
# bootstrap script; we now layer on what GitHub Actions needs to ship.
DEPLOY_ROLES=(
  "roles/run.admin"                       # update Cloud Run service
  "roles/compute.instanceAdmin.v1"        # restart VM container script
  "roles/iam.serviceAccountUser"          # let SA act as itself for Cloud Run
  "roles/artifactregistry.writer"         # docker push
  "roles/iap.tunnelResourceAccessor"      # SSH via IAP to VM
  "roles/cloudbuild.builds.editor"        # `gcloud builds submit`
  "roles/storage.admin"                   # tfstate bucket (terraform apply)
)
for role in "${DEPLOY_ROLES[@]}"; do
  gcloud projects add-iam-policy-binding "${GCP_PROJECT}" \
    --member="serviceAccount:${DEPLOYER_SA}" \
    --role="${role}" \
    --condition=None \
    --quiet >/dev/null
done
log "deploy roles applied to ${DEPLOYER_SA}"

# Cloud Run needs the deploying identity to be able to actAs the
# runtime identity. Since we're using the same SA, we still need to
# grant `serviceAccountUser` on itself so `gcloud run deploy` can
# attach the SA to the new revision.
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --member="serviceAccount:${DEPLOYER_SA}" \
  --role="roles/iam.serviceAccountUser" \
  --quiet >/dev/null
log "${DEPLOYER_SA} can actAs itself (Cloud Run revision deploy)"

# 5. Allow the GitHub repo principal to impersonate the deploy SA
PRINCIPAL_SET="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.repository/${GH_REPO}"
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="${PRINCIPAL_SET}" \
  --quiet >/dev/null
log "GitHub repo '${GH_REPO}' may now impersonate ${DEPLOYER_SA}"

# 6. Print what GitHub needs
cat <<EOF

────────────────────────────────────────────────────────────────────
  GitHub repo secrets to set (https://github.com/${GH_REPO}/settings/secrets/actions)
────────────────────────────────────────────────────────────────────

  GCP_WORKLOAD_IDENTITY_PROVIDER
    ${PROVIDER_RESOURCE}

  GCP_DEPLOYER_SA
    ${DEPLOYER_SA}

  GCP_PROJECT
    ${GCP_PROJECT}

  GCP_REGION
    us-central1

  GCP_ZONE
    us-central1-a

────────────────────────────────────────────────────────────────────
EOF
