# Gemini Enterprise License Provisioner for Google Workspace

An automated, serverless solution on Google Cloud Platform that manages and provisions Gemini Enterprise (Standard Tier) licenses to Google Workspace domain users based on Google Groups membership. Deploy it to any GCP project / Workspace domain — see [setup_instructions.md](setup_instructions.md).

---

## Target Architecture

- **Unified Web & Worker Service**: FastAPI application hosted on **GCP Cloud Run** providing both the administrative web dashboard and the scheduled sync endpoint (`POST /api/sync/run`).
- **Scheduled Trigger**: **GCP Cloud Scheduler** triggers the worker endpoint on a user-defined cron schedule with OIDC authentication.
- **Persistence & Auditing**: **GCP Firestore** in Native mode storing application configuration (`config/gemini_provisioner`) and complete execution run logs (`sync_history`).
- **Google Workspace Integration**: Domain-Wide Delegation (DWD) using a dedicated GCP Service Account to interact with the **Admin SDK Directory API** and **Enterprise License Manager API**.
- **CI/CD & IaC**: **GitHub Actions** pipeline authenticating via **Workload Identity Federation (WIF)** and deploying infrastructure via Terraform / `gcloud`.

```
                    ┌─────────────────────────┐
                    │  Google Cloud Scheduler │
                    └───────────┬─────────────┘
                                │ (Cron trigger via OIDC)
                                ▼
┌─────────────────────────────────────────────────────────────┐
│                 Cloud Run: Single Container                 │
│                                                             │
│   FastAPI Web Admin UI          Sync Worker Engine          │
│   • Dashboard & Analytics       • Flat Group Evaluation     │
│   • Group Selection UI          • Deduplicate User Emails   │
│   • Cron Schedule UI            • Check & Assign Licenses   │
│   • DWD Connectivity Test       • Flag Nested Groups        │
└──────────────┬──────────────────────────────┬───────────────┘
               │                              │
               ▼                              ▼
    ┌────────────────────┐      ┌───────────────────────────┐
    │  Cloud Firestore   │      │ Google Workspace APIs     │
    │  • config          │      │ • Admin Directory API     │
    │  • sync_history    │      │ • Enterprise Licensing    │
    └────────────────────┘      └───────────────────────────┘
```

---

## Key Design Principles & Simplifications

1. **Flat Group Membership (No Nesting)**:
   - Queries direct group members only.
   - If a member is a nested group (`type == "GROUP"`), the provisioner skips it, logs a clear warning, and records an explicit error in the sync history (`Nested groups are not supported for licensing`).
2. **Additive Provisioning (Deprovisioning Out of Scope)**:
   - Users in monitored groups receive the Gemini license if they do not already have it.
   - Deprovisioning is intentionally decoupled and left to standard enterprise offboarding.
3. **Zero-Build Modern Frontend**:
   - Server-rendered Jinja2 templates styled with Tailwind CSS via CDN. No Node.js, npm, or webpack pipeline needed.
4. **Keyless GitHub Actions Deployment**:
   - Uses Workload Identity Federation (WIF) — no long-lived service account keys stored in GitHub Secrets.
5. **Run Notifications**:
   - Optional post-run email (Gmail API via DWD) with full run detail and a link back to Run History; choose "all runs" or "failures only" on the Sync Schedule page.
6. **Super-admin-only access (optional)**:
   - Behind Identity-Aware Proxy, the app checks the IAP-asserted user is a Google Workspace super administrator before serving any page or mutating API.

---

## Directory Structure

```
gemini-license-provisioner/
├── .github/
│   └── workflows/
│       └── deploy.yml              # GitHub Actions CI/CD with WIF & Cloud Run deployment
├── app/
│   ├── static/
│   │   └── css/custom.css          # Styling touches & keyframes
│   ├── templates/
│   │   ├── base.html               # Shared layout (Tailwind CDN, navbar, alerts)
│   │   ├── dashboard.html          # Overview cards, quick trigger, last sync status
│   │   ├── groups.html             # List domain groups, select groups to track
│   │   ├── schedule.html           # Update sync frequency & Cloud Scheduler trigger
│   │   ├── settings.html           # DWD connectivity test, SKU config, admin email
│   │   └── history.html            # Sync run history table with error logs
│   ├── __init__.py
│   ├── main.py                     # FastAPI routes (UI + POST /api/sync/run)
│   ├── config.py                   # Environment & runtime settings
│   ├── auth.py                     # IAP JWT verification + Workspace super-admin check
│   ├── firestore_db.py             # Firestore client for config & history
│   ├── workspace_client.py         # Google Workspace DWD, Directory, Licensing & Gmail APIs
│   ├── notifications.py            # Post-run email reports (Gmail API via DWD)
│   ├── sync_worker.py              # Core sync engine (direct members, dedupe, assign SKU)
│   └── scheduler_service.py        # Programmatic Cloud Scheduler API client
├── terraform/
│   ├── main.tf                     # Cloud Run, Scheduler, IAM, Firestore, APIs
│   ├── variables.tf                # Parameter declarations (project_id, region, domain)
│   ├── outputs.tf                  # Web UI URL, Service Account email, WIF Provider ID
│   └── terraform.tfvars.example    # Starter variable values
├── tests/
│   ├── __init__.py
│   ├── test_sync.py                # Unit test for sync logic & nested group handling
│   └── test_api.py                 # FastAPI endpoint & view tests
├── scripts/
│   └── setup_wif.sh                # Helper script to bootstrap WIF for your GCP project
├── setup_instructions.md           # Step-by-step setup (GCP, DWD scopes, WIF, GitHub)
├── Dockerfile                      # Cloud Run container definition
├── requirements.txt                # Lean Python dependencies
└── .gitignore                      # Python, Terraform, GCP credential excludes
```

---

## Quickstart: Local Development & Testing

### 1. Set up Virtual Environment

```bash
cd gemini-license-provisioner
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Run Unit Tests

```bash
pytest tests/ -v
```

### 3. Run Application Locally

```bash
export GCP_PROJECT_ID="your-gcp-project-id"
export DELEGATED_ADMIN_EMAIL="workspace-admin@your-domain.com"                 # a real, active admin user
export RUNTIME_SERVICE_ACCOUNT_EMAIL="<sa-name>@<your-gcp-project-id>.iam.gserviceaccount.com"
export PRODUCT_ID="Google-Apps"
export SKU_ID="101031"

uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Visit `http://localhost:8080` to access the Admin Web UI.

---

## Deployment to GCP

For complete, step-by-step instructions — filling in your own project, region, service
account, and Workspace domain — see [setup_instructions.md](setup_instructions.md).
[`EXAMPLE_DEPLOYMENT.md`](EXAMPLE_DEPLOYMENT.md) shows one set of values filled in end to end.

### Privileges required to deploy

**Google Cloud** (operator, on your project): `roles/owner`, or the granular set of
`serviceusage.serviceUsageAdmin`, `datastore.owner`, `iam.serviceAccountAdmin`,
`resourcemanager.projectIamAdmin`, `run.admin`, `artifactregistry.admin`,
`cloudscheduler.admin`, and `iam.workloadIdentityPoolAdmin`.

**Google Cloud** (the app service account, used for both runtime and CI/CD):
`datastore.user`, `cloudscheduler.admin`, `logging.logWriter`, `run.admin`,
`artifactregistry.admin`, `iam.serviceAccountUser`, and
`iam.serviceAccountTokenCreator` **on itself** (for keyless DWD).

**Google Workspace**: a **Super Admin** to register the Domain-Wide Delegation entry, plus a
real, active, licensed delegated-admin user for the service to impersonate (with Directory
read and license-management privileges — Super Admin covers these).

Full breakdown with per-role rationale: [setup_instructions.md → Required Privileges](setup_instructions.md#required-privileges).

### Security model

Optional two-layer access control: **Identity-Aware Proxy** authenticates the browser,
then the **app verifies the IAP assertion and requires the user to be a Google Workspace
super administrator** (Directory `isAdmin`), returning `403` otherwise. The Cloud
Scheduler service account is allowed through `POST /api/sync/run` only.

Enforcement turns on when `IAP_AUDIENCE` is set (`enable_iap = true` in Terraform).
**Until then the UI and every `/api/*` endpoint are public** (`--allow-unauthenticated`
+ `allUsers` invoker) — fine only for a first smoke test. Full setup:
[setup_instructions.md → Security Model](setup_instructions.md#security-model).
