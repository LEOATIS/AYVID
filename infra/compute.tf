###############################################################################
# Compute Engine VM hosting the IBKR Gateway, paper runner, and the C++
# kill-switch watchdog. Has to be a long-lived VM (NOT Cloud Run) because:
#
#   - IB TWS / Gateway holds a stateful TCP socket
#   - C++ watchdog needs O_DIRECT mmap access to /tmp/ayvid_risk.mmap
#   - Cron-style jobs (graph propose, graph refine) run on a fixed schedule
#
# Boot script installs Docker, pulls the execution image, and starts:
#   1. ibgateway         (head-less IBC + IB Gateway)
#   2. ayvid-runner      (Python paper runner)
#   3. ayvid-watchdog    (compiled C++ kill switch with libcurl IB flatten)
###############################################################################

resource "google_service_account_iam_member" "vm_uses_sa" {
  service_account_id = "projects/${var.project_id}/serviceAccounts/${var.service_account_email}"
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${var.service_account_email}"
}

resource "google_compute_instance" "execution" {
  name = "ayvid-execution"
  # P1-deploy: switched from n2-standard-4 to e2-standard-4 because
  # n2 inventory in us-central1 was depleted at apply time. e2 is the
  # same vCPU/memory class (4/16) on a different hardware pool, costs
  # ~30% less, and is fine for IBC + paper-runner — n2's only
  # advantage was AVX-512 which neither IBC nor the runner uses.
  machine_type = "e2-standard-4"
  zone         = var.zone

  tags = ["ayvid", "execution", "ibkr"]

  boot_disk {
    initialize_params {
      image = "projects/cos-cloud/global/images/family/cos-stable"
      size  = 50
      type  = "pd-balanced"
    }
  }

  network_interface {
    network = "default"
    access_config {
      // Ephemeral public IP — IBC needs outbound to gdc.ibllc.com:4001.
      // Inbound is firewalled to 22 + 8000 only.
    }
  }

  service_account {
    email  = var.service_account_email
    scopes = ["cloud-platform"]
  }

  metadata = {
    "google-logging-enabled" = "true"
  }

  metadata_startup_script = <<-EOT
    #!/bin/bash
    set -euo pipefail
    set -x

    REGION="${var.region}"
    PROJECT="${var.project_id}"
    IMAGE="${"$REGION"}-docker.pkg.dev/${"$PROJECT"}/ayvid/ayvid-execution:${var.image_tag}"

    # Container-Optimized OS has Docker pre-installed but does NOT
    # ship gcloud. Authenticate Docker against Artifact Registry by
    # asking the metadata server for an OAuth2 access token (the VM's
    # service account has roles/artifactregistry.reader from main.tf).
    docker --version
    METADATA_HEADER="Metadata-Flavor: Google"
    METADATA_BASE="http://metadata.google.internal/computeMetadata/v1"
    ACCESS_TOKEN=$(curl -sf -H "$METADATA_HEADER" \
        "$METADATA_BASE/instance/service-accounts/default/token" \
        | tr -d '\n' \
        | sed -n 's/.*"access_token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    if [ -z "$ACCESS_TOKEN" ]; then
        echo "FATAL: could not obtain access token from metadata server" >&2
        exit 1
    fi
    # Container-Optimized OS has a read-only root filesystem, so the
    # default docker config dir (/root/.docker) cannot be written.
    # Pin DOCKER_CONFIG to /var/lib (writable on COS) and use it
    # consistently for login + pull + run below.
    export DOCKER_CONFIG=/var/lib/docker-config
    mkdir -p "$DOCKER_CONFIG"
    chmod 700 "$DOCKER_CONFIG"
    echo "$ACCESS_TOKEN" | docker --config "$DOCKER_CONFIG" login \
        -u oauth2accesstoken --password-stdin "$REGION-docker.pkg.dev"

    # Pull every secret into env-file form for the container. We hit
    # the Secret Manager REST API directly with the same access token
    # because gcloud is unavailable on COS.
    SECRETS_FILE=/var/run/ayvid.env
    : > $SECRETS_FILE
    for secret_id in ${join(" ", [for s in google_secret_manager_secret.ayvid : s.secret_id])}; do
        env_name=$(echo "$secret_id" | sed 's/^ayvid-//' | tr '[:lower:]-' '[:upper:]_')
        # `access` returns a JSON object with base64-encoded data; decode it.
        encoded=$(curl -sf -H "Authorization: Bearer $ACCESS_TOKEN" \
            "https://secretmanager.googleapis.com/v1/projects/$PROJECT/secrets/$secret_id/versions/latest:access" \
            | tr -d '\n' \
            | sed -n 's/.*"data"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
        if [ -z "$encoded" ]; then
            echo "WARN: secret $secret_id returned empty payload" >&2
            continue
        fi
        value=$(echo "$encoded" | base64 -d)
        printf '%s=%s\n' "$env_name" "$value" >> $SECRETS_FILE
    done
    # Shared live-artifact location so VM writer + Cloud Run reader
    # look at the same source of truth.
    printf 'AYVID_LIVE_GCS_BUCKET=%s\n' "${var.project_id}-tfstate" >> $SECRETS_FILE
    printf 'AYVID_LIVE_GCS_PREFIX=%s\n' "live_state" >> $SECRETS_FILE
    chmod 600 $SECRETS_FILE

    # --- IB Gateway container ----------------------------------------------
    # Uses gnzsnz/ib-gateway image:
    #   https://github.com/gnzsnz/ib-gateway-docker/pkgs/container/ib-gateway
    #
    # We map host 127.0.0.1:7497 -> container 4002 (paper API port).
    # AYVID runner then connects to IB_HOST/IB_PORT from secrets.
    IBGW_IMAGE="ghcr.io/gnzsnz/ib-gateway:latest"
    IBGW_USER=$(grep -E '^IB_GATEWAY_USERNAME=' "$SECRETS_FILE" | cut -d= -f2- || true)
    IBGW_PASS=$(grep -E '^IB_GATEWAY_PASSWORD=' "$SECRETS_FILE" | cut -d= -f2- || true)
    IBGW_MODE=$(grep -E '^IB_GATEWAY_TRADING_MODE=' "$SECRETS_FILE" | cut -d= -f2- || true)
    [ -z "$IBGW_MODE" ] && IBGW_MODE="paper"

    docker --config "$DOCKER_CONFIG" pull "$IBGW_IMAGE"
    docker rm -f ib-gateway 2>/dev/null || true
    if [ -n "$IBGW_USER" ] && [ -n "$IBGW_PASS" ] && [ "$IBGW_USER" != "placeholder" ] && [ "$IBGW_PASS" != "placeholder" ]; then
        docker run -d --restart=always --name ib-gateway \
            -e TWS_USERID="$IBGW_USER" \
            -e TWS_PASSWORD="$IBGW_PASS" \
            -e TRADING_MODE="$IBGW_MODE" \
            -e READ_ONLY_API=no \
            -e AUTO_RESTART_TIME="23:45" \
            -p 127.0.0.1:7497:4002 \
            "$IBGW_IMAGE"
    else
        echo "WARN: IB_GATEWAY_USERNAME/PASSWORD missing; IB gateway container not started."
    fi

    # Wait up to ~5 minutes for IB port readiness if gateway was started.
    ib_ready=false
    for _ in $(seq 1 60); do
        if timeout 1 bash -c "echo > /dev/tcp/127.0.0.1/7497" >/dev/null 2>&1; then
            ib_ready=true
            break
        fi
        sleep 5
    done

    # --- AYVID runner container --------------------------------------------
    docker --config "$DOCKER_CONFIG" pull "$IMAGE"
    docker rm -f ayvid-execution 2>/dev/null || true

    RUNNER_ARGS=(python -m cli.app trade paper-start --equity 1000000 --interval 60)
    if [ "$ib_ready" != "true" ]; then
        echo "WARN: IB gateway not ready on 127.0.0.1:7497; starting AYVID runner in --shadow mode."
        RUNNER_ARGS=(python -m cli.app trade paper-start --shadow --equity 1000000 --interval 60)
    fi

    docker run -d --restart=always --name ayvid-execution \
        --env-file $SECRETS_FILE \
        --cap-add=SYS_PTRACE \
        --tmpfs /tmp:exec,size=128m \
        -p 8001:8001 \
        "$IMAGE" \
        "$${RUNNER_ARGS[@]}"
  EOT

  scheduling {
    preemptible         = false
    automatic_restart   = true
    on_host_maintenance = "MIGRATE"
  }

  depends_on = [
    google_artifact_registry_repository.ayvid_docker,
    google_secret_manager_secret_iam_member.backend_can_read_secrets,
  ]
}

resource "google_compute_firewall" "execution_ssh" {
  # P1-16: the previous default was source_ranges = ["0.0.0.0/0"],
  # which exposed the IBKR-connected host to the internet on port 22
  # — worst-possible posture for a box that holds live broker creds
  # and an mmap'd kill-switch. The rule is now driven by the
  # `ssh_source_ranges` variable (default: empty), so operators MUST
  # either pass explicit CIDRs or tunnel through IAP (recommended).
  count   = length(var.ssh_source_ranges) == 0 ? 0 : 1
  name    = "ayvid-execution-ssh"
  network = "default"

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }

  source_ranges = var.ssh_source_ranges
  target_tags   = ["execution"]
}

output "execution_vm_name" {
  description = "Compute Engine VM name running the IB Gateway + paper runner + C++ kill switch."
  value       = google_compute_instance.execution.name
}

output "execution_vm_external_ip" {
  description = "External IP address of the execution VM."
  value       = google_compute_instance.execution.network_interface[0].access_config[0].nat_ip
}
