terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.15.0"
    }
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
    "admin.googleapis.com",
    "licensing.googleapis.com",
    "cloudscheduler.googleapis.com",
    "firestore.googleapis.com",
    "run.googleapis.com",
    "artifactregistry.googleapis.com",
    "iamcredentials.googleapis.com",
    "cloudbuild.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each                   = toset(locals.services)
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
  repository_id = "gemini-provisioner-docker"
  description   = "Docker repository for Gemini Enterprise license provisioner"
  format        = "DOCKER"
}

# -----------------------------------------------------------------------------
# 3. Service Accounts & IAM Permissions
# -----------------------------------------------------------------------------

# Application Service Account (Cloud Run)
resource "google_service_account" "app_sa" {
  account_id   = "sa-gemini-provisioner"
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
  account_id   = "sa-scheduler-invoker"
  display_name = "Cloud Scheduler Invoker Service Account"
  description  = "Used by Cloud Scheduler to invoke the /api/sync/run endpoint with OIDC auth"
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
        name  = "DELEGATED_ADMIN_EMAIL"
        value = var.delegated_admin_email
      }
      env {
        name  = "RUNTIME_SERVICE_ACCOUNT_EMAIL"
        value = google_service_account.app_sa.email
      }
      env {
        name  = "PRODUCT_ID"
        value = var.product_id
      }
      env {
        name  = "SKU_ID"
        value = var.sku_id
      }
      env {
        name  = "CLOUD_SCHEDULER_JOB_NAME"
        value = "gemini-license-sync-job"
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

# Allow Scheduler SA to invoke Cloud Run
resource "google_cloud_run_v2_service_iam_member" "scheduler_invoker" {
  location = google_cloud_run_v2_service.provisioner.location
  name     = google_cloud_run_v2_service.provisioner.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.scheduler_sa.email}"
}

# Note: For admin web UI access, allow allUsers or bind to internal corporate identities/IAP
resource "google_cloud_run_v2_service_iam_member" "public_access" {
  location = google_cloud_run_v2_service.provisioner.location
  name     = google_cloud_run_v2_service.provisioner.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# -----------------------------------------------------------------------------
# 5. Cloud Scheduler Job
# -----------------------------------------------------------------------------
resource "google_cloud_scheduler_job" "sync_job" {
  depends_on  = [google_project_service.apis, google_cloud_run_v2_service.provisioner]
  name        = "gemini-license-sync-job"
  description = "Triggers Google Workspace Gemini license provisioning based on Google Groups"
  schedule    = var.initial_cron_expression
  time_zone   = "UTC"
  region      = var.region

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.provisioner.uri}/api/sync/run"

    headers = {
      "Content-Type" = "application/json"
    }

    body = base64encode(jsonencode({
      "triggered_by" = "scheduled"
    }))

    oidc_token {
      service_account_email = google_service_account.scheduler_sa.email
      audience              = google_cloud_run_v2_service.provisioner.uri
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
