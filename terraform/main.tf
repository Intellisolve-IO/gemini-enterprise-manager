terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.20.0"
    }
  }

  # Backend blocks can't use variable interpolation - this is intentionally
  # hardcoded, unlike everything else in this file. Edit directly if you fork
  # this repo again for a different deployment.
  backend "gcs" {
    bucket = "intellisolve-ge-console-tfstate"
    prefix = "gemini-enterprise-manager"
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# -----------------------------------------------------------------------------
# 1. Enable Required GCP APIs
# -----------------------------------------------------------------------------
locals {
  services = [
    "admin.googleapis.com",            # Admin SDK Directory (group / user reads via DWD)
    "discoveryengine.googleapis.com",  # Gemini Enterprise license configs + user licenses
    "cloudscheduler.googleapis.com",
    "firestore.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudbuild.googleapis.com",
    "gmail.googleapis.com",            # only used for run-notification emails
    "compute.googleapis.com",          # Load Balancer resources for the App URL Mapping module
  ]
}

resource "google_project_service" "apis" {
  for_each                   = toset(local.services)
  project                    = var.project_id
  service                    = each.key
  disable_on_destroy         = false
  disable_dependent_services = false
}

# -----------------------------------------------------------------------------
# 2. Artifact Registry Repository
# -----------------------------------------------------------------------------
resource "google_artifact_registry_repository" "docker_repo" {
  depends_on    = [google_project_service.apis]
  location      = var.region
  repository_id = var.artifact_repository_id
  description   = "Docker repository for Gemini Enterprise license provisioner"
  format        = "DOCKER"
}

# -----------------------------------------------------------------------------
# 3. Service Accounts & IAM Permissions
# -----------------------------------------------------------------------------

# Application Service Account (Cloud Run)
resource "google_service_account" "app_sa" {
  account_id   = var.app_service_account_id
  display_name = "Gemini License Provisioner Application Service Account"
  description  = "Runs Cloud Run service, manages Firestore config, and calls Workspace APIs via DWD"
}

# Grant Firestore (Datastore User) access
resource "google_project_iam_member" "sa_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# Grant Cloud Scheduler Admin (to modify schedule trigger via API)
resource "google_project_iam_member" "sa_scheduler_admin" {
  project = var.project_id
  role    = "roles/cloudscheduler.admin"
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# Grant Cloud Logging Writer
resource "google_project_iam_member" "sa_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# Manage Gemini Enterprise licenses (list license configs, list/assign user
# licenses via the Discovery Engine API). This is called with the service
# account's own credentials - not Domain-Wide Delegation.
resource "google_project_iam_member" "sa_gemini_licenses" {
  project = var.project_id
  role    = "roles/discoveryengine.admin"
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# Firebase Authentication: mint/verify session cookies from the Admin SDK
# (app/core/firebase_auth.py, initialize_app() with no arguments, so it runs
# as this service account via Application Default Credentials). Without this,
# create_session_cookie() fails server-side with INSUFFICIENT_PERMISSION and
# sign-in never completes, even though the client-side ID token exchange
# itself succeeds.
resource "google_project_iam_member" "sa_firebase_auth_admin" {
  project = var.project_id
  role    = "roles/firebaseauth.admin"
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# Health Check module: read-only IAM policy + enabled-API introspection.
# Both are read-only predefined roles - no change to the service account's
# write-level blast radius.
resource "google_project_iam_member" "sa_iam_security_reviewer" {
  project = var.project_id
  role    = "roles/iam.securityReviewer"
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

resource "google_project_iam_member" "sa_serviceusage_viewer" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageViewer"
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# App URL Mapping module: full CRUD on Load Balancer resources (static IPs,
# managed certs, URL maps, target proxies, forwarding rules) so the module can
# provision them directly at runtime. This is the broadest grant in this
# config - roles/compute.loadBalancerAdmin is project-scoped, not scoped to
# resources this app created, so the service account can also modify any
# *other* load balancer in the project. Accepted for now to match the app's
# existing single-service-account pattern; see setup_instructions.md's
# Security Model for the full rationale. Gated behind a variable (default
# false) so enabling it is a deliberate, separate decision from deploying the
# rest of the app.
resource "google_project_iam_member" "sa_load_balancer_admin" {
  count   = var.enable_url_mapping_module ? 1 : 0
  project = var.project_id
  role    = "roles/compute.loadBalancerAdmin"
  member  = "serviceAccount:${google_service_account.app_sa.email}"
}

# Allow the application service account to mint signed JWTs as itself (IAM Credentials
# API: signBlob). This is what enables keyless Google Workspace Domain-Wide Delegation
# from Cloud Run - the app signs a JWT asserting the delegated-admin subject and
# exchanges it for an access token, with no exported service account key.
resource "google_service_account_iam_member" "sa_token_creator_self" {
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.app_sa.email}"
}

# Cloud Scheduler Invoker Service Account
resource "google_service_account" "scheduler_sa" {
  account_id   = var.scheduler_service_account_id
  display_name = "Cloud Scheduler Invoker Service Account"
  description  = "Used by Cloud Scheduler to execute the license-sync Cloud Run Job via the Cloud Run Admin API"
}

# -----------------------------------------------------------------------------
# 4. Cloud Run Service
# -----------------------------------------------------------------------------
resource "google_cloud_run_v2_service" "provisioner" {
  depends_on = [
    google_project_service.apis,
    google_project_iam_member.sa_firestore,
    google_service_account_iam_member.sa_token_creator_self
  ]
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  # Public by design: this is a multi-tenant, self-serve SaaS surface. Every
  # request is authorized in-app via a Firebase session cookie (see
  # app/core/session_auth.py) plus per-tenant Firestore membership, not by a
  # network-level gate - Identity-Aware Proxy's pre-authorized-principal model
  # doesn't fit self-serve signup, so this app doesn't front itself with IAP.

  template {
    service_account = google_service_account.app_sa.email
    timeout         = "600s" # 10-minute timeout for large directory sync runs

    scaling {
      min_instance_count = 0
      max_instance_count = 5
    }

    containers {
      image = var.container_image

      resources {
        limits = {
          cpu    = "1000m"
          memory = "1024Mi"
        }
      }

      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "RUNTIME_SERVICE_ACCOUNT_EMAIL"
        value = google_service_account.app_sa.email
      }
      env {
        name  = "PUBLIC_BASE_URL"
        value = var.public_base_url
      }
      env {
        name  = "FIREBASE_API_KEY"
        value = var.firebase_api_key
      }
      env {
        name  = "FIREBASE_PROJECT_ID"
        value = var.firebase_project_id
      }
      env {
        name  = "CLOUD_SCHEDULER_JOB_NAME"
        value = var.scheduler_job_name
      }
      env {
        name  = "CLOUD_SCHEDULER_LOCATION"
        value = var.region
      }

      ports {
        container_port = 8080
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 3
      }
    }
  }
}

# Public invoker binding - the web service is unconditionally public; every
# request is authorized in-app (see the note on the service resource above).
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  location = google_cloud_run_v2_service.provisioner.location
  name     = google_cloud_run_v2_service.provisioner.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# 4b. Custom Domain Mapping (optional)
# -----------------------------------------------------------------------------
# Requires the domain to already be verified for this GCP account in Search
# Console (https://search.google.com/search-console) - a one-time, account-
# level step Terraform cannot perform. See setup_instructions.md's "Custom
# Domain" section.
resource "google_cloud_run_domain_mapping" "custom_domain" {
  count    = var.custom_domain != "" ? 1 : 0
  location = var.region
  name     = var.custom_domain

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.provisioner.name
  }
}

# -----------------------------------------------------------------------------
# 5. Scheduled license sync: a Cloud Run Job triggered by Cloud Scheduler
# -----------------------------------------------------------------------------
# The scheduled run executes as a Cloud Run *Job* (the same image, command
# `python -m app.job_runner`), triggered by Cloud Scheduler through the Cloud
# Run Admin API - this path never touches the web service's own HTTP routes.
#
# KNOWN LIMITATION (multi-tenant conversion, Phase 1): this job still syncs
# exactly ONE designated tenant/environment (scheduled_sync_tenant_id /
# scheduled_sync_environment_id), not a fan-out across every tenant's
# environments. Replacing this with a tick job that scans all due
# environments is Phase 5 of the multi-tenant conversion - see
# app/job_runner.py's module docstring.
resource "google_cloud_run_v2_job" "sync_runner" {
  depends_on = [
    google_project_service.apis,
    google_project_iam_member.sa_firestore,
    google_service_account_iam_member.sa_token_creator_self,
  ]
  name     = var.sync_job_name
  location = var.region

  template {
    template {
      service_account = google_service_account.app_sa.email
      timeout         = "1800s"
      max_retries     = 0 # Cloud Scheduler retries the trigger; don't double up

      containers {
        image   = var.container_image
        command = ["python", "-m", "app.job_runner"]

        resources {
          limits = {
            cpu    = "1000m"
            memory = "1024Mi"
          }
        }

        env {
          name  = "GCP_PROJECT_ID"
          value = var.project_id
        }
        env {
          name  = "GCP_REGION"
          value = var.region
        }
        env {
          name  = "RUNTIME_SERVICE_ACCOUNT_EMAIL"
          value = google_service_account.app_sa.email
        }
        env {
          name  = "PUBLIC_BASE_URL"
          value = var.public_base_url
        }
        env {
          name  = "SCHEDULED_SYNC_TENANT_ID"
          value = var.scheduled_sync_tenant_id
        }
        env {
          name  = "SCHEDULED_SYNC_ENVIRONMENT_ID"
          value = var.scheduled_sync_environment_id
        }
      }
    }
  }

  # CI redeploys the job image on each release; ignore drift on the image here.
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }
}

# Cloud Scheduler's SA may execute (run) the job, nothing more.
resource "google_cloud_run_v2_job_iam_member" "scheduler_runs_job" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.sync_runner.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

resource "google_cloud_scheduler_job" "sync_job" {
  depends_on  = [google_project_service.apis, google_cloud_run_v2_job.sync_runner]
  name        = var.scheduler_job_name
  description = "Runs the Gemini Enterprise license-sync Cloud Run Job on a cron schedule"
  schedule    = var.initial_cron_expression
  time_zone   = "UTC"
  region      = var.region

  # Trigger the Cloud Run Job via the Admin API. Target is a *.googleapis.com
  # endpoint, so it authenticates with an OAuth access token (not an OIDC token).
  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.sync_runner.name}:run"

    headers = {
      "Content-Type" = "application/json"
    }

    # The Admin API's jobs:run rejects an empty body with 400 INVALID_ARGUMENT;
    # RunJobRequest with no overrides is just "{}".
    body = base64encode("{}")

    oauth_token {
      service_account_email = google_service_account.scheduler_sa.email
    }
  }
}

# -----------------------------------------------------------------------------
# 6. Workload Identity Federation (GitHub Actions CI/CD)
# -----------------------------------------------------------------------------
resource "google_iam_workload_identity_pool" "github_pool" {
  count                     = var.github_repo != "" ? 1 : 0
  depends_on                = [google_project_service.apis]
  workload_identity_pool_id = "github-actions-pool"
  display_name              = "GitHub Actions Pool"
  description               = "Workload Identity Pool for GitHub Actions automated deployments"
}

resource "google_iam_workload_identity_pool_provider" "github_provider" {
  count                              = var.github_repo != "" ? 1 : 0
  workload_identity_pool_id          = google_iam_workload_identity_pool.github_pool[0].workload_identity_pool_id
  workload_identity_pool_provider_id = "github-provider"
  display_name                       = "GitHub Provider"
  
  attribute_mapping = {
    "google.subject"             = "assertion.sub"
    "attribute.actor"            = "assertion.actor"
    "attribute.repository"       = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
  }

  attribute_condition = "assertion.repository == '${var.github_repo}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Allow GitHub Actions to impersonate Application SA for deployment
resource "google_service_account_iam_member" "github_sa_user" {
  count              = var.github_repo != "" ? 1 : 0
  service_account_id = google_service_account.app_sa.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github_pool[0].name}/attribute.repository/${var.github_repo}"
}
