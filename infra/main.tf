###############################################################################
# AYVID — root Terraform module.
#
# Bootstraps the project's GCP / AWS dual-cloud surface:
#
#   - Cloud Run service for the FastAPI backend           (cloudrun.tf)
#   - Compute Engine VM for the IBKR + watchdog stack     (compute.tf)
#   - Artifact Registry repo for Docker images
#   - Secret Manager secrets for OPENAI / ANTHROPIC / IBKR creds
#   - State backend in a remote GCS bucket so multiple operators can
#     plan/apply without trampling each other's local .tfstate
#
# Usage:
#   terraform -chdir=infra init -backend-config="bucket=$AYVID_TFSTATE_BUCKET"
#   terraform -chdir=infra plan  -var "project_id=$GCP_PROJECT" -var "region=us-central1"
#   terraform -chdir=infra apply -var "project_id=$GCP_PROJECT" -var "region=us-central1"
###############################################################################

terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.40"
    }
  }

  # Remote state lives in GCS so plan/apply is multi-operator-safe.
  # Bucket name is supplied at `terraform init` time via -backend-config.
  backend "gcs" {
    prefix = "ayvid/terraform"
  }
}

###############################################################################
# Variables
###############################################################################

variable "project_id" {
  type        = string
  description = "GCP project ID hosting AYVID."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Default region for Cloud Run + Artifact Registry."
}

variable "zone" {
  type        = string
  default     = "us-central1-a"
  description = "Zone for the Compute Engine watchdog VM."
}

variable "service_account_email" {
  type        = string
  description = "Pre-created service account that backend + worker run as."
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag for both backend and execution images."
}

###############################################################################
# Security / ingress variables (P1-16)
###############################################################################

variable "backend_public_invoker" {
  type        = bool
  default     = false
  description = <<EOT
If true, Cloud Run binds run.invoker to allUsers (fully public backend).
This is NEVER what you want in production — AYVID exposes operational
data (equity curves, priors, SSE trade signals). Default is false:
only explicit IAM members listed in `backend_invoker_members` may
invoke the service. Flip to true only for an unauthenticated public
read surface when the marketing site is hosted on the same endpoint.
EOT
}

variable "backend_invoker_members" {
  type        = list(string)
  default     = []
  description = <<EOT
IAM members allowed to invoke the Cloud Run backend. Typical entries:
  * user:alice@example.com
  * group:ayvid-ops@example.com
  * serviceAccount:frontend-sa@project.iam.gserviceaccount.com
Ignored when backend_public_invoker=true.
EOT
}

variable "ssh_source_ranges" {
  type        = list(string)
  default     = []
  description = <<EOT
CIDR ranges allowed to SSH into the execution VM. EMPTY by default so
the firewall rule is created with no ingress — operators must either
set this explicitly or use IAP-tunneled SSH (recommended). Passing
["0.0.0.0/0"] makes the box world-reachable on port 22 and is
unconditionally a bad idea for an IBKR-connected host.
EOT
}

###############################################################################
# Provider
###############################################################################

provider "google" {
  project = var.project_id
  region  = var.region
  zone    = var.zone
}

###############################################################################
# Artifact Registry — Docker repo for ayvid-backend / ayvid-execution images
###############################################################################

resource "google_artifact_registry_repository" "ayvid_docker" {
  location      = var.region
  repository_id = "ayvid"
  description   = "AYVID container images (backend + execution + frontend)"
  format        = "DOCKER"
}

###############################################################################
# Secret Manager — credentials never live in env files
###############################################################################

locals {
  # Secret IDs are designed so the auto-generated Cloud Run env var name
  # (uppercased, dashes → underscores) matches what `shared/config.py`
  # expects. Examples:
  #   ayvid-openai-api-key     →  OPENAI_API_KEY
  #   ayvid-neo4j-uri          →  NEO4J_URI
  #   ayvid-ib-account         →  IB_ACCOUNT
  #   ayvid-api-key            →  API_KEY
  #
  # If you add a new field to `shared.config.Settings`, add the matching
  # secret-id here and `scripts/deploy_bootstrap.sh` will pick the value
  # out of `.env` automatically.
  secret_ids = [
    # LLM
    "openai-api-key",
    "anthropic-api-key",
    # OpenBB data feeds
    "openbb-fmp-api-key",
    "openbb-fred-api-key",
    "openbb-benzinga-api-key",
    "openbb-tiingo-api-key",
    "openbb-intrinio-api-key",
    # News / feeds
    "finnhub-api-key",
    # Graph DB (Neo4j Aura — managed)
    "neo4j-uri",
    "neo4j-username",
    "neo4j-password",
    # Vector DB (Qdrant Cloud — managed)
    "qdrant-url",
    "qdrant-api-key",
    # Feature store
    "hopsworks-api-key",
    # Broker
    "ib-account",
    "ib-host",
    "ib-port",
    "paper-trading",
    "auto-execute",
    "ib-gateway-username",
    "ib-gateway-password",
    "ib-gateway-trading-mode",
    "ib-client-portal-cookie",
    # API gateway
    "api-key",
  ]
}

resource "google_secret_manager_secret" "ayvid" {
  for_each  = toset(local.secret_ids)
  secret_id = "ayvid-${each.key}"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "backend_can_read_secrets" {
  for_each  = google_secret_manager_secret.ayvid
  secret_id = each.value.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.service_account_email}"
}

# `secretAccessor` lets the runtime read individual known-name
# secrets but NOT enumerate the secret list — the
# `secrets.list` permission lives in `secretmanager.viewer`.
# scripts/vm_redeploy.sh uses live API listing to discover newly-
# added secrets without a code change, so without this binding
# every redeploy 403'd on the list call, the secrets-rewrite loop
# saw zero IDs, and the runner came back online with an empty env
# file (no OPENAI_API_KEY → every LLM call 401 → zero signals
# minted). Granting list at the project level is intentional: it
# leaks only the *names* of secrets, never values, and makes the
# auto-discovery loop in vm_redeploy.sh actually work.
resource "google_project_iam_member" "runtime_can_list_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.viewer"
  member  = "serviceAccount:${var.service_account_email}"
}

###############################################################################
# Outputs
###############################################################################

output "artifact_registry_repository" {
  description = "Full Docker registry URL for AYVID images."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.ayvid_docker.repository_id}"
}

output "managed_secret_ids" {
  description = "Secret Manager IDs created for backend/worker consumption."
  value       = [for s in google_secret_manager_secret.ayvid : s.secret_id]
}
