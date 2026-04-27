###############################################################################
# Cloud Run service for the AYVID FastAPI backend.
#
# This is the read-mostly surface — health checks, market-data lookups,
# graph queries, calibration artefacts. The IB execution loop and the
# C++ kill-switch live on the Compute Engine VM defined in compute.tf.
###############################################################################

resource "google_cloud_run_v2_service" "backend" {
  name     = "ayvid-backend"
  location = var.region

  template {
    service_account = var.service_account_email

    scaling {
      min_instance_count = 1
      max_instance_count = 8
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.ayvid_docker.repository_id}/ayvid-backend:${var.image_tag}"

      resources {
        cpu_idle = false
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
        startup_cpu_boost = true
      }

      ports {
        container_port = 8000
      }

      # Inject every Secret Manager secret as a Cloud Run env var. The
      # backend reads them via shared.config.Settings just like in dev.
      dynamic "env" {
        for_each = google_secret_manager_secret.ayvid
        content {
          name = upper(replace(env.key, "-", "_"))
          value_source {
            secret_key_ref {
              secret  = env.value.secret_id
              version = "latest"
            }
          }
        }
      }

      env {
        name  = "PAPER_TRADING"
        value = "true"
      }

      env {
        name  = "LOG_LEVEL"
        value = "INFO"
      }

      # CORS: explicit allowlist for the Vercel-hosted frontend. The
      # default in `shared.config.Settings` is `*`, which the CORS spec
      # rejects when combined with credentials. Production must name
      # the origins it serves.
      env {
        name  = "AYVID_ALLOWED_ORIGINS"
        value = "https://ayvid.ai,https://www.ayvid.ai,https://app.ayvid.ai"
      }

      # Non-secret defaults that match the user's `.env`. These keep the
      # backend config legible — anything sensitive is in Secret Manager
      # via the dynamic block above; anything in plaintext lives here.
      env {
        name  = "LLM_PROVIDER"
        value = "openai"
      }
      env {
        name  = "OPENAI_MODEL"
        value = "gpt-4o"
      }
      env {
        name  = "NEO4J_DATABASE"
        value = "neo4j"
      }
      env {
        name  = "QDRANT_COLLECTION"
        value = "ayvid_events"
      }
      env {
        name  = "HOPSWORKS_PROJECT"
        value = "ayvid_featurestore"
      }
      env {
        name  = "SEC_EDGAR_EMAIL"
        value = "research@ayvid.ai"
      }

      # P1-15 / P1-16: force strict auth in deployed environments.
      # Local dev and tests leave AYVID_REQUIRE_AUTH=false so they work
      # without registering keys; Cloud Run MUST run with auth strict
      # so `verify_api_key` rejects anonymous requests even if a
      # dev-mode degrade ever slipped back into the code path.
      env {
        name  = "AYVID_REQUIRE_AUTH"
        value = "true"
      }

      startup_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        period_seconds        = 5
        failure_threshold     = 12
        timeout_seconds       = 3
        initial_delay_seconds = 5
      }

      liveness_probe {
        http_get {
          path = "/health"
          port = 8000
        }
        period_seconds    = 30
        failure_threshold = 5
        timeout_seconds   = 5
      }
    }

    timeout = "60s"
  }

  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }
}

# P1-16: Cloud Run IAM posture.
#
# The previous configuration unconditionally bound `allUsers` to
# `run.invoker` — making the backend, SSE, priors, and live-trade
# endpoints reachable anonymously. The application layer DOES gate
# mutations behind X-API-Key, but (a) that gate degrades to dev-mode
# when AYVID_REQUIRE_AUTH is not set, and (b) read-only endpoints
# that expose equity curves / priors / shock intel ran without any
# check at all.
#
# New default (var.backend_public_invoker = false): we only bind the
# members explicitly listed in `var.backend_invoker_members`. Flip
# the flag to true ONLY if you accept a fully public backend. Even
# then, set AYVID_REQUIRE_AUTH=true in the Cloud Run env so the
# application layer rejects anonymous requests.

resource "google_cloud_run_v2_service_iam_member" "public_invoker" {
  count    = var.backend_public_invoker ? 1 : 0
  name     = google_cloud_run_v2_service.backend.name
  location = google_cloud_run_v2_service.backend.location
  role     = "roles/run.invoker"
  member   = "allUsers"
}

resource "google_cloud_run_v2_service_iam_member" "restricted_invokers" {
  for_each = toset(var.backend_public_invoker ? [] : var.backend_invoker_members)
  name     = google_cloud_run_v2_service.backend.name
  location = google_cloud_run_v2_service.backend.location
  role     = "roles/run.invoker"
  member   = each.value
}

output "backend_url" {
  description = "Public URL of the Cloud Run backend."
  value       = google_cloud_run_v2_service.backend.uri
}

###############################################################################
# Custom domain — `api.ayvid.ai` -> ayvid-backend on Cloud Run.
#
# Cloud Run domain mappings require the apex/SLD to be domain-verified
# against the project (Search Console TXT record). Once verified, GCP
# returns A/AAAA/CNAME records that we paste into Cloudflare DNS with
# the proxy DISABLED — Cloud Run terminates TLS itself and Cloudflare
# orange-cloud will fight the cert handshake.
#
# The `output "api_domain_records"` below prints the records to add.
###############################################################################

resource "google_cloud_run_domain_mapping" "api" {
  location = var.region
  name     = "api.ayvid.ai"

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.backend.name
  }
}

output "api_domain_records" {
  description = "DNS records to add in Cloudflare (proxy OFF) to bind api.ayvid.ai to Cloud Run."
  value       = google_cloud_run_domain_mapping.api.status[0].resource_records
}
