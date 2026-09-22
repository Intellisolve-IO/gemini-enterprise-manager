# Gemini Enterprise Admin Console

A modular, multi-tenant admin console for **Gemini Enterprise**: license provisioning,
environment health checks, app URL mapping, and (planned) agent deployment, all behind
one self-serve web app. Each customer (**tenant**) signs in with Google via Firebase
Auth, creates one or more **environments** (one per GCP project / Gemini Enterprise
app), and turns on whichever modules they need — no fork, no redeploy per customer.

Deploy one instance of this app; any number of tenants use it against their own GCP
projects. Start with **[setup_instructions.md](setup_instructions.md)**;
**[EXAMPLE_DEPLOYMENT.md](EXAMPLE_DEPLOYMENT.md)** is the same guide with every value
filled in.

---

## What it does

The console itself is a thin shell — sign-in, tenant/environment picker, module grid,
and an onboarding gate. The actual work happens in per-tenant, per-environment
**modules**:

| Module | Status | What it does |
| :--- | :--- | :--- |
| **License Sync** | Enabled by default | Reads direct members of configured Google Groups, dedupes them, skips anyone already licensed, and batch-assigns the rest a Gemini Enterprise license via the Discovery Engine API. Runs on a schedule or on demand; every run is recorded with counts, timing, and per-item errors, with an optional email report. |
| **Health Check** | Opt-in per environment | Audits a Gemini Enterprise environment's IAM bindings, enabled APIs, and DWD/impersonation connectivity; renders a pass/fail report. |
| **App URL Mapping** | Opt-in per environment | Maps a custom domain to a Gemini Enterprise app deep link via GCP Load Balancer resources. |
| **Agent Deployment** | Registered, not yet built | Placeholder in the module registry (`app/core/modules.py`) — no router mounted yet. |

License Sync's own scope, unchanged from earlier single-tenant versions:

- **No deprovisioning.** Licenses are only added; removal is normal offboarding.
- **No nested-group expansion.** A group containing another group is skipped and
  flagged as an error (`Nested groups are not supported for licensing`).
- **No user/directory changes.** It only reads the directory and manages Gemini
  Enterprise licenses.

---

## Multi-tenancy model

```
tenant (a company)
 └─ environment (one GCP project + optionally one GE app)
     ├─ module_config/{module_id}   -- enabled flag per module
     ├─ config/license_sync         -- that module's own settings
     ├─ sync_history/*, health_check_runs/*, ...
     └─ status: "onboarding" | "active"
```

- **Tenant** — the unit of billing/membership. Members have role `owner`, `admin`, or
  `member` (`app/core/tenants.py`). A user can belong to multiple tenants.
- **Environment** — one GCP project a tenant wants this console to manage, with its own
  Google-managed **service account** and Gemini Enterprise app. A tenant can have several
  (e.g. separate departments or projects), each isolated from the others.
- **Onboarding gate** — a new environment starts `status="onboarding"`. Its module grid
  is unreachable (redirected to `/settings`) until the environment's service account is
  configured and a live impersonation test passes, at which point `status` flips to
  `active` and `onboarded_at` is stamped (`app/landing.py`, Phase 3 work).
- **Cross-project impersonation** — the central app never holds a standing credential in
  a tenant's project. It impersonates *that environment's own service account*
  (`app/core/tenant_credentials.py`) for every call into that project, using
  `roles/iam.serviceAccountTokenCreator` the tenant grants it during onboarding. The one
  exception is "tenant-zero": an environment with no `sa_email` configured yet falls back
  to the central app's own runtime service account, so local testing and the very first
  environment work before onboarding is wired up.
- **Known gap (tracked, not yet built):** scheduled License Sync runs still target a
  single designated tenant/environment (`SCHEDULED_SYNC_TENANT_ID` /
  `SCHEDULED_SYNC_ENVIRONMENT_ID`), not a per-environment fan-out — see
  `app/job_runner.py` and `terraform/variables.tf`. Ad-hoc "Run Sync Now" from the UI
  already works per-environment; only the *scheduled* trigger has this limitation.

---

## Architecture

```
                                   cron → Cloud Run Admin API (:run, one designated
   ┌───────────────────────┐        tenant/environment until the fan-out phase lands)
   │   Cloud Scheduler     │──────────────────────────────┐
   └───────────────────────┘                              ▼
                                          ┌──────────────────────────────┐
   Google Sign-In (Firebase Auth) ──►     │  Cloud Run Job               │
        session cookie, browser only      │  `python -m app.job_runner`  │
                          │               │  → runs the sync engine      │
                          ▼               └──────────────┬───────────────┘
   ┌─────────────────────────────────────────────────────────┐
   │  Cloud Run — one container (FastAPI), public ingress      │
   │                                                           │
   │  Landing / auth (app/, no module prefix)                  │
   │  • Sign-in gate • Tenant picker/create                    │
   │  • Environment picker/create • Module grid                │
   │  • Environment Settings (onboarding gate, SA + test)      │
   │                                                           │
   │  Feature modules, mounted under                           │
   │  /t/{tenant_id}/e/{environment_id}/modules/*               │
   │  • license-sync   • health-check   • url-mapping           │
   └───────┬──────────────────┬───────────────────┬────────────┘
           ▼                  ▼                   ▼
   ┌──────────────┐   ┌──────────────────────┐  ┌───────────────────────┐
   │  Firestore   │   │ Impersonated creds    │  │ Firebase Auth         │
   │ • tenants/   │   │ (per-environment SA,  │  │ • ID token verify     │
   │   .../envs/  │   │  optionally + DWD     │  │ • session cookie      │
   │   .../config │   │  subject) →           │  │   mint/verify         │
   │   .../history│   │  Workspace Directory, │  └───────────────────────┘
   │ • user_index │   │  Discovery Engine,    │
   └──────────────┘   │  Compute, Service     │
                       │  Usage, IAM APIs      │
                       └──────────────────────┘
   Build/deploy: GitHub Actions → Workload Identity Federation → Artifact Registry → Cloud Run
```

| Component | Role |
| :--- | :--- |
| **Cloud Run service** (`app/`) | Single FastAPI container: sign-in/landing, tenant & environment picker, module grid, environment settings/onboarding, and every enabled module's UI + API |
| **Cloud Run job** (`app/job_runner.py`) | Same image, `python -m app.job_runner`; runs License Sync for the one tenant/environment named by `SCHEDULED_SYNC_TENANT_ID`/`SCHEDULED_SYNC_ENVIRONMENT_ID` — no HTTP in the path |
| **Cloud Scheduler** | On a cron schedule, calls the Cloud Run Admin API to execute the job |
| **Firestore** (Native mode) | `tenants/{id}`, `tenants/{id}/members/{uid}`, `tenants/{id}/environments/{id}` and everything nested under an environment (module config, sync history, health-check runs); `user_index/{uid}` for fast login → tenant lookup |
| **Firebase Authentication** | Google sign-in in the browser (Firebase JS SDK popup); the backend verifies the resulting ID token and mints an `HttpOnly` session cookie (`app/auth_routes.py`, `app/core/firebase_auth.py`, `app/core/session_auth.py`) |
| **Cross-project impersonation** | The central app's own runtime identity impersonates each environment's tenant-owned service account (`roles/iam.serviceAccountTokenCreator`, keyless, via the IAM Credentials API) to call that project's APIs — Domain-Wide Delegation is layered on top of the *same* impersonation call (a `subject`) when a module needs to act as a Workspace user |
| **Gemini Enterprise licensing** | The impersonated environment service account calls the **Discovery Engine API** directly (no DWD) to list license subscriptions and check/assign user licenses |
| **GitHub Actions + WIF** | Builds the image and deploys to Cloud Run with no long-lived keys |
| **Terraform** (`terraform/`) | Reference infrastructure-as-code for the central app's own project (Cloud Run, Firestore-adjacent IAM, Artifact Registry, WIF, the scheduled job); it does **not** provision anything in a tenant's own project — that's the onboarding flow's job |

---

## Repository layout

```
├── app/
│   ├── main.py                  # FastAPI app: router wiring, module-disabled handler, /healthz
│   ├── config.py                # Central-app-wide settings only (env-var backed)
│   ├── landing.py                # Sign-in gate, tenant/environment picker+create, module grid, onboarding gate
│   ├── auth_routes.py            # POST /auth/session, /auth/logout (Firebase session cookie)
│   ├── core/
│   │   ├── firebase_auth.py      # Firebase Admin SDK wrapper: verify ID token, mint/verify session cookie
│   │   ├── session_auth.py       # Session-cookie dependency + tenant/environment membership checks
│   │   ├── tenants.py            # Tenant/environment/membership Firestore data layer
│   │   ├── tenant_credentials.py # Cross-project SA impersonation (+ optional DWD subject)
│   │   ├── modules.py            # Static module registry (id, title, icon, base path, default-enabled)
│   │   ├── module_config.py      # Per-environment, per-module "enabled" + settings Firestore doc
│   │   ├── module_auth.py        # FastAPI dependencies gating a module's routes on it being enabled
│   │   ├── rendering.py          # Jinja2 template rendering helper
│   │   └── templating.py         # Jinja2 environment setup
│   ├── modules/
│   │   ├── license_sync/router.py       # Dashboard, groups, schedule, settings, history, sync API
│   │   ├── health_check/{router,checks,iam_client}.py  # IAM/API/DWD audit + report
│   │   └── url_mapping/{router,compute_client}.py      # Custom-domain → GE app Load Balancer mapping
│   ├── workspace_client.py       # DWD-via-impersonation credentials + Directory / Gmail clients
│   ├── gemini_licensing.py       # Gemini Enterprise license configs + user licenses (Discovery Engine)
│   ├── sync_worker.py            # Core License Sync engine
│   ├── job_runner.py             # Batch entrypoint for scheduled sync (Cloud Run job)
│   ├── notifications.py          # Post-run email report (Gmail API)
│   ├── firestore_db.py           # Environment-scoped config + run-history persistence
│   ├── scheduler_service.py      # Reads/updates the Cloud Scheduler job from the UI
│   ├── templates/*.html          # Server-rendered Jinja2 views (Tailwind via CDN), one subtree per module
│   └── static/css/custom.css
├── tests/                        # pytest; tests/core/ and tests/modules/ mirror app/core and app/modules
├── scripts/
│   ├── setup_wif.sh              # One-time Workload Identity Federation bootstrap
│   └── test_local.py             # Dependency-free smoke test of the sync engine
├── terraform/                    # main.tf, variables.tf, outputs.tf, terraform.tfvars.example
├── .github/workflows/deploy.yml  # Build + deploy pipeline (repo-variable driven)
├── Dockerfile
├── requirements.txt
├── setup_instructions.md         # Full deployment guide
└── EXAMPLE_DEPLOYMENT.md         # The guide with example values filled in
```

---

## Dependencies

### Runtime (Python, `requirements.txt`)

| Package | Why |
| :--- | :--- |
| `fastapi`, `uvicorn[standard]` | Web framework + ASGI server |
| `jinja2`, `python-multipart` | Server-rendered templates, form parsing |
| `pydantic`, `pydantic-settings` | Typed settings from environment variables |
| `google-cloud-firestore` | Tenant/environment/config/history data |
| `google-cloud-scheduler` | Read/update the schedule from the UI |
| `google-cloud-compute` | App URL Mapping module's Load Balancer resources |
| `firebase-admin` | Verify Firebase ID tokens, mint/verify session cookies |
| `google-api-python-client` | Admin SDK Directory, Gmail, Discovery Engine, Service Usage |
| `google-auth`, `google-auth-oauthlib`, `google-auth-httplib2` | `google.auth.impersonated_credentials` for cross-project/DWD impersonation |
| `requests` | Transitive HTTP needs |
| `pytest`, `httpx` | Test suite only (kept here for convenience) |

Front-end assets (Tailwind, Font Awesome) load from public CDNs at page render time — no
Node/npm build.

### Google Cloud APIs (central app's own project)

`run`, `cloudscheduler`, `firestore`, `artifactregistry`, `cloudbuild`, `iam`,
`iamcredentials`, `admin` (Admin SDK Directory), `discoveryengine`, `gmail` (report
emails), `compute` (App URL Mapping). Each **tenant's** project needs the subset its
enabled modules use, enabled on that project — the onboarding flow guides this per
environment.

### Google Cloud resources (central app's own project)

One project with billing, a Firestore database (Native mode), an Artifact Registry
Docker repo, one runtime service account (also used by CI), a Cloud Scheduler service
account, a Cloud Run service, a Cloud Run job (scheduled sync), a Cloud Scheduler job
(the cron trigger), and a Workload Identity pool/provider for GitHub Actions. All
created by `setup_instructions.md` (or `terraform/`). A tenant's **own** GCP project
needs none of this — only a service account the central app can impersonate.

### Firebase

A Firebase project (usually the same GCP project as above) with **Google** enabled as a
sign-in provider under Authentication. See
[setup_instructions.md](setup_instructions.md).

### Google Workspace (per tenant, only if that tenant enables Directory-backed features)

- A real, active, licensed Workspace user the environment's service account impersonates
  via DWD to read groups/members (License Sync) and optionally send report email.
- A **Gemini Enterprise** subscription in that tenant's GCP project with free seats.

### CI/CD

GitHub Actions with repository **secrets** `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT` and the
repository **variables** in the table below.

---

## Configuration reference

Environment variables on the Cloud Run **service** configure the **central app itself**
— nothing tenant- or environment-specific lives here anymore; that all lives in
Firestore under `tenants/{id}/environments/{id}/...` (see
[Multi-tenancy model](#multi-tenancy-model)).

| Variable | Required | Default | Purpose |
| :--- | :--- | :--- | :--- |
| `GCP_PROJECT_ID` | yes | — | The central app's own project, for Firestore/Scheduler clients |
| `GCP_REGION` | no | `us-central1` | Region for the Scheduler client |
| `FIRESTORE_DATABASE` | no | `(default)` | Firestore database id |
| `RUNTIME_SERVICE_ACCOUNT_EMAIL` | yes (on Cloud Run) | — | The central app's own attached service account; the source identity every impersonation call starts from |
| `FIREBASE_API_KEY` | yes (for sign-in) | — | Firebase Web API key, embedded client-side (not secret) |
| `FIREBASE_PROJECT_ID` | yes (for sign-in) | — | Firebase project id; also used to verify session cookies server-side |
| `FIREBASE_AUTH_DOMAIN` | no | `{FIREBASE_PROJECT_ID}.firebaseapp.com` | Override if you use a custom Firebase Auth domain |
| `CLOUD_SCHEDULER_JOB_NAME` | no | `gemini-license-sync-job` | Cron trigger job the UI reads/updates |
| `CLOUD_SCHEDULER_LOCATION` | no | `us-central1` | Region of that scheduler job |
| `SCHEDULED_SYNC_TENANT_ID` / `SCHEDULED_SYNC_ENVIRONMENT_ID` | no | empty | Which single tenant/environment the scheduled Cloud Run job syncs (see the fan-out gap noted above) — the job exits 1 every run until both are set |
| `PUBLIC_BASE_URL` | no | learned from traffic | Base URL for links in emails; set explicitly behind a custom domain |
| `SERVICE_ACCOUNT_KEY_JSON` / `SERVICE_ACCOUNT_KEY_PATH` | no | unset | Local dev only: a key for the central app's own source credentials, instead of ambient/metadata-server auth |
| `DEBUG` | no | `false` | Verbose logging; also re-enables `/docs`, `/redoc`, `/openapi.json` |

Repository variables consumed by `.github/workflows/deploy.yml`: `GCP_PROJECT_ID`
(required), `GCP_REGION`, `CLOUD_RUN_SERVICE`, `ARTIFACT_REPO`, `CLOUD_SCHEDULER_JOB`,
`CLOUD_RUN_JOB`, `FIREBASE_API_KEY`, `FIREBASE_PROJECT_ID`, `PUBLIC_BASE_URL`,
`SCHEDULED_SYNC_TENANT_ID`, `SCHEDULED_SYNC_ENVIRONMENT_ID`.

Per-environment, per-module settings (delegated admin email, monitored groups, license
subscription, notification recipients, cron expression, service account email, etc.) are
all set from the app itself — the module's Settings page, or the environment's own
Settings/onboarding page — and persisted to Firestore, not to env vars.

---

## Local development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pytest tests/ -v              # unit tests, no cloud access
python scripts/test_local.py  # dependency-mocked smoke test of the sync engine
```

To run the app locally against real Google APIs you need Application Default
Credentials for the central app's own identity, plus a Firebase project for sign-in:

```bash
export GCP_PROJECT_ID="your-project"
export FIREBASE_API_KEY="..."
export FIREBASE_PROJECT_ID="your-project"
export SERVICE_ACCOUNT_KEY_JSON="$(cat path/to/central-app-sa-key.json)"   # or `gcloud auth application-default login`
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Without Firebase config the sign-in page still renders but sign-in fails. Without cloud
credentials the UI still renders; calls that hit Google APIs will error. An environment
with no `sa_email` configured (tenant-zero, or before onboarding) falls back to the
central app's own identity, so you can exercise a module end-to-end without a second GCP
project.

---

## Deployment

See **[setup_instructions.md](setup_instructions.md)** for the full walkthrough
(Firebase project, GCP project, Firestore, service account, Workload Identity
Federation, GitHub Actions, first tenant/environment, and per-environment onboarding). A
pushed commit to `main` builds and deploys automatically.

### Privileges to deploy (summary)

- **GCP operator**: `roles/owner`, or `serviceusage.serviceUsageAdmin` +
  `datastore.owner` + `iam.serviceAccountAdmin` + `resourcemanager.projectIamAdmin` +
  `run.admin` + `artifactregistry.admin` + `cloudscheduler.admin` +
  `iam.workloadIdentityPoolAdmin`.
- **Central app's runtime/CI service account**: `datastore.user`,
  `cloudscheduler.admin`, `logging.logWriter`, `firebaseauth.admin` (session-cookie
  minting for sign-in), `run.admin`, `artifactregistry.admin`, `iam.serviceAccountUser`,
  and `iam.serviceAccountTokenCreator` **on itself** (its own keyless impersonation for
  tenant-zero / self-service).
- **Per tenant, per environment**: the tenant grants the central app's runtime service
  account `roles/iam.serviceAccountTokenCreator` **on their own environment's service
  account**, and grants that service account whatever project roles its enabled modules
  need (Discovery Engine, Directory-via-DWD, Compute, IAM read, etc.) — walked through by
  the in-app onboarding flow, not by this repo's Terraform.

Full rationale: [setup_instructions.md → Required Privileges](setup_instructions.md#required-privileges).

---

## Security model

- **Authentication**: Firebase Authentication (Google sign-in). The browser runs
  `signInWithPopup(new GoogleAuthProvider())`, gets an ID token, and POSTs it to
  `/auth/session`; the backend verifies it and sets an `HttpOnly` session cookie
  (`ge_session`, 5-day lifetime). No IAP, no network-level gate — see the note on
  `google_cloud_run_v2_service` in `terraform/main.tf`: this is a public, self-serve SaaS
  surface by design, not a single pre-authorized organization.
- **Authorization**: every tenant- or environment-scoped route depends on Firestore
  membership (`app/core/session_auth.py`) — a signed-in user only sees tenants they're a
  member of, and `owner`/`admin` role is required for tenant-management actions. A
  disabled module's routes 403 (API) or redirect with a toast (page), even for members.
- **Cross-project trust is narrow and revocable**: the central app never holds a
  standing broad credential in a tenant's project. It only ever impersonates that
  environment's own service account, and only for as long as the tenant leaves
  `roles/iam.serviceAccountTokenCreator` granted. Revoking that one binding cuts off
  access to that environment immediately.
- **Scheduled sync** does not go through the web service or a browser session — Cloud
  Scheduler executes the Cloud Run job directly, for the one tenant/environment
  configured via `SCHEDULED_SYNC_TENANT_ID`/`SCHEDULED_SYNC_ENVIRONMENT_ID`.
- `GET /healthz` backs Cloud Run's own startup probe and is reachable internally without
  auth — but Cloud Run intentionally blocks *external* traffic to whatever path is
  configured as the startup probe, so it 404s from outside even when the service is
  healthy. Use `GET /` for an external liveness check instead. DWD/impersonated
  credentials are never exposed to the browser.

Full setup and hardening notes: [setup_instructions.md → Security Model](setup_instructions.md#security-model).

---

## Operations

- **Onboarding**: a freshly created environment lands on its **Settings** page and
  cannot reach the module grid until it configures a service account and passes a live
  connection test (**Complete Setup**), which flips `status` to `active`.
- **Run history** (License Sync): the module's **History** page (and `sync_history` in
  Firestore, under that environment) has every run with status, counts, timing, and
  per-item errors. Failures don't stop the service — a run that hits errors is recorded
  `FAILED` or `PARTIAL_SUCCESS`; the next scheduled run proceeds normally.
- **Notifications** (optional, License Sync): configure recipients on the module's
  **Sync Schedule** page; choose "all runs" or "failures only". Requires the
  `gmail.send` DWD scope on that environment.
- **Toggle a module**: flip it on/off per environment from the module grid — no
  redeploy, just a Firestore write (`app/core/module_config.py`).
- **Logs**: `gcloud run services logs read <service> --region <region>`; app loggers are
  namespaced `gemini_provisioner.*`.

---

## Testing

`pytest tests/` covers: the tenant/environment/membership data layer and cross-project
impersonation (`tests/core/`), each feature module (`tests/modules/`), the landing/auth
flow (`test_landing.py`, `test_auth_routes.py`, `test_module_auth.py`,
`test_module_config.py`), the License Sync engine (dedupe, nested-group handling), the
notification builder/dispatch, and the Workspace client. `scripts/test_local.py` runs
the sync engine with all cloud dependencies mocked.

---

## Status

Open source, run without a formal SLA. Actively evolving: the codebase is mid-way
through a conversion from a single-tenant, IAP-fronted tool into this multi-tenant
console (data model, Firebase auth, and cross-project impersonation are done; a
per-environment scheduling fan-out and a fuller guided-onboarding wizard are tracked but
not yet built — see the module docstrings for specifics, e.g. `app/job_runner.py` and
`app/landing.py`). Review
[setup_instructions.md → Security Model](setup_instructions.md#security-model) before
any non-trivial use, and treat every service account here (central app and per-tenant)
as privileged.

---

## Contributing

Bug reports, feature requests, and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) for how to propose a change and the local dev/test
setup. Every pull request requires review from a code owner (see
[`.github/CODEOWNERS`](.github/CODEOWNERS)) before it can be merged. Found a security
issue? See [SECURITY.md](SECURITY.md) instead of filing a public issue.

## License

Licensed under the [Apache License 2.0](LICENSE).
