# Setup Instructions: Gemini Enterprise Admin Console

This is a two-part setup:

- **Part A — deploy the console** (once, by whoever operates it): a GCP project,
  Firestore, a runtime service account, Firebase Authentication, Workload Identity
  Federation, and GitHub Actions. Steps 1–7 below.
- **Part B — onboard a tenant/environment** (once per customer/project the console will
  manage): sign in, create a tenant and an environment, then grant that environment's
  own service account the narrow, revocable access the console needs into *that*
  project. Step 8 below. This is done almost entirely in the app itself.

Every command in Part A uses shell variables for values specific to **your** deployment.
See [`EXAMPLE_DEPLOYMENT.md`](EXAMPLE_DEPLOYMENT.md) for one filled-in example of both
parts.

---

## 0. Fill in your values (Part A — the console's own project)

```bash
# ── GCP (the console's own project) ─────────────────────────────────────────────
export PROJECT_ID="your-gcp-project-id"            # GCP project the console runs in
export REGION="us-central1"                        # region for Cloud Run / Artifact Registry / Scheduler
export SA_NAME="sa-gemini-provisioner"             # name for the app + CI/CD service account (created below)
export ARTIFACT_REPO="gemini-provisioner-docker"   # Artifact Registry repository name
export SERVICE_NAME="gemini-license-provisioner"   # Cloud Run service name
export SCHEDULER_JOB="gemini-license-sync-job"     # Cloud Scheduler job (the cron trigger)
export SYNC_JOB="gemini-license-sync-runner"       # Cloud Run job the schedule executes

# ── GitHub ────────────────────────────────────────────────────────────────────
export GITHUB_REPO="your-org/your-repo"            # repo that hosts this code (used by WIF)

# ── Derived (do not edit) ─────────────────────────────────────────────────────
export SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud auth login
gcloud config set project "${PROJECT_ID}"
```

Nothing here is a Workspace domain or a tenant's own project — those are configured
per-tenant in Part B, inside the app.

If you're using a **Terraform remote state backend** or a **custom domain**, also pick a
globally-unique GCS bucket name and a hostname now — see the `backend "gcs"` block in
`terraform/main.tf` and the [Custom Domain](#custom-domain-optional) section below.

---

## Prerequisites

1. **GCP project** `${PROJECT_ID}` with billing enabled, and an operator identity
   holding the roles under [Required Privileges](#required-privileges).
2. **A Firebase project** — usually `${PROJECT_ID}` itself, added to Firebase via the
   [Firebase console](https://console.firebase.google.com/), with **Google** enabled as
   a sign-in provider.
3. **GitHub repository** `${GITHUB_REPO}` with permission to add Actions secrets and
   variables.
4. **Local tooling**: `gcloud` and `git`.

Nothing in Part A requires a Google Workspace super admin or a Domain-Wide Delegation
grant — those only come up per-tenant, in Part B, and only for tenants that enable the
License Sync module.

---

## Required Privileges

### Google Cloud — operator (the person running Steps 1–6)

Granted on project `${PROJECT_ID}`. Either `roles/owner`, or this least-privilege set:

| Role | Needed for |
| :--- | :--- |
| `roles/serviceusage.serviceUsageAdmin` | Enable APIs (Step 1) |
| `roles/datastore.owner` | Create the Firestore database (Step 2) |
| `roles/iam.serviceAccountAdmin` | Create the service account and set IAM policy **on** it (Steps 3, 6) |
| `roles/resourcemanager.projectIamAdmin` | Grant project roles to the service account (Steps 3, 6) |
| `roles/artifactregistry.admin` | Create the Docker repository (Terraform / first deploy) |
| `roles/run.admin` | Create the Cloud Run service (first deploy) |
| `roles/iam.serviceAccountUser` on the runtime service account | Deploy Cloud Run "acting as" that account |
| `roles/cloudscheduler.admin` | Create the Cloud Scheduler job |
| `roles/iam.workloadIdentityPoolAdmin` | Create the WIF pool/provider (Step 6) |

### Google Cloud — the console's own service account `${SA_NAME}@${PROJECT_ID}` (runtime **and** CI/CD)

One service account runs the Cloud Run service, is impersonated by GitHub Actions to
deploy it, and — for **tenant-zero** (self-testing before any real tenant onboards) —
impersonates *itself* to exercise every module. Steps 3 and 6 (`scripts/setup_wif.sh`)
grant the baseline roles; two more (marked below) need a separate command because the
script predates them.

| Role | Scope | Purpose |
| :--- | :--- | :--- |
| `roles/datastore.user` | project | Read/write tenant, environment, config, and history data in Firestore |
| `roles/discoveryengine.admin` | project | Tenant-zero: list Gemini Enterprise license subscriptions; check/assign user licenses under the console's own project |
| `roles/cloudscheduler.admin` | project | The **Sync Schedule** page edits the scheduler job at runtime |
| `roles/logging.logWriter` | project | Structured logs |
| `roles/iam.serviceAccountTokenCreator` | **on itself** | Sign JWTs for keyless impersonation — both tenant-zero self-impersonation and the identity every cross-project impersonation call starts from |
| `roles/iam.securityReviewer` **(not in `setup_wif.sh` yet — grant separately)** | project | Tenant-zero: read-only IAM policy inspection for the **Health Check** module |
| `roles/serviceusage.serviceUsageViewer` **(not in `setup_wif.sh` yet — grant separately)** | project | Tenant-zero: read-only enabled-API inspection for the **Health Check** module |
| `roles/compute.loadBalancerAdmin` **(opt-in, not in `setup_wif.sh`)** | project | Tenant-zero: provision Load Balancer resources for the **App URL Mapping** module. Project-scoped — it lets the service account manage *any* load balancer of these types in the project. Only grant it if you intend to exercise that module under the console's own project. |
| `roles/run.admin` | project | GitHub Actions deploys new revisions |
| `roles/iam.serviceAccountUser` | on itself | GitHub Actions deploys Cloud Run as this account |
| `roles/artifactregistry.admin` | project | GitHub Actions pushes container images |

> For a stricter setup, split deployment onto a separate CI service account holding only
> `run.admin`, `artifactregistry.writer`, and `iam.serviceAccountUser` on the runtime
> account, and drop `run.admin` / `artifactregistry.admin` / `iam.serviceAccountUser`
> from the runtime account.

**None of the roles above touch a tenant's own project.** For every real tenant
environment, the console instead *impersonates that environment's own service account*
— see [Part B](#step-8-onboard-a-tenantenvironment) and
[Required Privileges — per-tenant](#google-cloud--per-tenant-environment-service-account).

### Google Cloud — per-tenant environment service account

Created by the tenant, in the tenant's **own** GCP project, during onboarding (Part B).
Not part of this app's Terraform. See [Step 8](#step-8-onboard-a-tenantenvironment) for
the exact commands and which roles each module needs.

### Firebase

Google sign-in enabled on the Firebase project, and the **Web API key** / **project ID**
from Firebase console → Project settings → General. Neither is secret (public,
client-side identifiers, same as any Firebase web app) — see
[Step 4](#step-4-set-up-firebase-authentication).

---

## Step 1: Enable Required Google Cloud APIs

```bash
gcloud services enable \
  discoveryengine.googleapis.com \
  cloudscheduler.googleapis.com \
  firestore.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudbuild.googleapis.com \
  admin.googleapis.com \
  gmail.googleapis.com \
  compute.googleapis.com \
  --project="${PROJECT_ID}"
```

`admin` (Directory), `gmail`, and `compute` are only exercised if you use tenant-zero to
test License Sync notifications or App URL Mapping under the console's own project;
harmless to enable up front.

---

## Step 2: Initialize Cloud Firestore in Native Mode

If Firestore is not yet active in `${PROJECT_ID}`:

1. In the Google Cloud Console, open **Firestore**.
2. Click **Create Database**.
3. Select **Firestore Native mode**.
4. Choose a location (use `${REGION}` or your preference) and database id `(default)`.
5. Click **Create Database**.

---

## Step 3: Create the Console's Service Account & Grant GCP Roles

This one account both **runs** the Cloud Run service and is impersonated by
**GitHub Actions** to deploy it; see [Required Privileges](#required-privileges) for the
rationale behind each role. `scripts/setup_wif.sh` (Step 6) applies the baseline
project-level bindings, so you can skip straight to Step 6 and come back for the two
extra roles below.

```bash
# Create the service account
gcloud iam service-accounts create "${SA_NAME}" \
  --display-name="Gemini Enterprise Admin Console Service Account" \
  --project="${PROJECT_ID}"

# Baseline project-level roles (runtime + CI/CD deploy + tenant-zero)
for ROLE in \
  roles/datastore.user \
  roles/discoveryengine.admin \
  roles/cloudscheduler.admin \
  roles/logging.logWriter \
  roles/iam.securityReviewer \
  roles/serviceusage.serviceUsageViewer \
  roles/run.admin \
  roles/artifactregistry.admin \
  roles/iam.serviceAccountUser
do
  gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
    --member="serviceAccount:${SA_EMAIL}" --role="${ROLE}" --condition=None
done

# Optional: only if you'll exercise the App URL Mapping module under tenant-zero
# gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
#   --member="serviceAccount:${SA_EMAIL}" --role="roles/compute.loadBalancerAdmin" --condition=None

# Allow the service account to mint signed JWTs as itself (keyless impersonation).
# Required both for tenant-zero self-impersonation and as the base identity every
# cross-project impersonation call starts from.
gcloud iam service-accounts add-iam-policy-binding "${SA_EMAIL}" \
  --member="serviceAccount:${SA_EMAIL}" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project="${PROJECT_ID}"
```

> **Runtime environment variables** the Cloud Run service needs:
> - `RUNTIME_SERVICE_ACCOUNT_EMAIL` = the service account email (`${SA_EMAIL}`) — the
>   identity every impersonation call starts from.
> - `GCP_PROJECT_ID`, `GCP_REGION` — the console's own project and region.
> - `FIREBASE_API_KEY`, `FIREBASE_PROJECT_ID` — from [Step 4](#step-4-set-up-firebase-authentication).
> - *(optional)* `PUBLIC_BASE_URL` — public https URL of the service, for links in
>   notification emails; the app also learns this from web traffic.
>
> Terraform and the GitHub Actions workflow set these for you. If you run
> `gcloud run deploy` by hand, pass them all in `--set-env-vars`.

---

## Step 4: Set up Firebase Authentication

1. Open the [Firebase console](https://console.firebase.google.com/) and add Firebase to
   `${PROJECT_ID}` (or create a new Firebase project pointed at it — same GCP project
   either way).
2. **Build → Authentication → Get started**, then enable **Google** under Sign-in
   method.
3. **Project settings → General → Your apps**: add a **Web app** (no Firebase Hosting
   needed). Note its:
   - **Web API key** → `FIREBASE_API_KEY`
   - **Project ID** → `FIREBASE_PROJECT_ID` (normally `${PROJECT_ID}`)

Neither value is secret — both are public, client-side identifiers embedded in the
sign-in page, the same as any Firebase web app. The backend verifies every session
server-side against the Firebase project itself
(`app/core/firebase_auth.py`) using the console's own Application Default Credentials —
no separate Firebase service-account key is needed.

---

## Step 5: Configure Workload Identity Federation (WIF) for GitHub Actions

WIF lets GitHub Actions deploy without a long-lived service-account key.

```bash
cd gemini-enterprise-manager
PROJECT_ID="${PROJECT_ID}" SA_NAME="${SA_NAME}" REGION="${REGION}" \
  bash scripts/setup_wif.sh "${GITHUB_REPO}"
```

`setup_wif.sh` creates the service account (if missing), applies its baseline
project-level roles, grants `roles/iam.serviceAccountTokenCreator` on the account to
itself, and creates the Workload Identity pool/provider. (It does **not** yet grant
`roles/iam.securityReviewer` or `roles/serviceusage.serviceUsageViewer` — add those with
the loop in [Step 3](#step-3-create-the-consoles-service-account--grant-gcp-roles) if
you haven't already.)

It prints two values. Add them under **GitHub → Settings → Secrets and variables →
Actions → Repository secrets**:

| Secret | Value |
| :--- | :--- |
| `WIF_PROVIDER` | `projects/<PROJECT_NUMBER>/locations/global/workloadIdentityPools/github-actions-pool/providers/github-provider` |
| `WIF_SERVICE_ACCOUNT` | the service account email (`${SA_EMAIL}`) |

Then add the deployment **Repository variables** (same screen, **Variables** tab):

| Variable | Value | Default if unset |
| :--- | :--- | :--- |
| `GCP_PROJECT_ID` | `${PROJECT_ID}` | *(required)* |
| `GCP_REGION` | `${REGION}` | `us-central1` |
| `CLOUD_RUN_SERVICE` | `${SERVICE_NAME}` | `gemini-license-provisioner` |
| `ARTIFACT_REPO` | `${ARTIFACT_REPO}` | `gemini-provisioner-docker` |
| `CLOUD_SCHEDULER_JOB` | `${SCHEDULER_JOB}` | `gemini-license-sync-job` |
| `CLOUD_RUN_JOB` | `${SYNC_JOB}` | `gemini-license-sync-runner` |
| `FIREBASE_API_KEY` | from Step 4 | *(required for sign-in to work)* |
| `FIREBASE_PROJECT_ID` | from Step 4 | *(required for sign-in to work)* |

The workflow also forwards these optional variables when set: `PUBLIC_BASE_URL`,
`SCHEDULED_SYNC_TENANT_ID`, `SCHEDULED_SYNC_ENVIRONMENT_ID` (see
[Scheduled Sync](#scheduled-sync)). Full list and meanings:
[README → Configuration reference](README.md#configuration-reference).

---

## Step 6: Push Code to Trigger Deployment

```bash
cd gemini-enterprise-manager
git remote add origin "https://github.com/${GITHUB_REPO}.git"   # if not already set
git push origin main
```

Pushing to `main` runs `.github/workflows/deploy.yml`: build the image, push it to
Artifact Registry, deploy the Cloud Run **service** (sign-in, tenant/environment picker,
module grid, and every module's UI/API), and deploy the Cloud Run **job** `${SYNC_JOB}`
(`python -m app.job_runner`) that Cloud Scheduler runs on a cron. See
[Scheduled Sync](#scheduled-sync) for the one-time scheduler wiring.

---

## Custom Domain (optional)

Map a domain you control to the Cloud Run service instead of using its `*.run.app` URL.
Needs a live Cloud Run service to target, so do this after Step 6.

1. **Verify domain ownership** in [Search Console](https://search.google.com/search-console),
   under the **same Google account** used for the GCP project. This is a one-time,
   account-level step Google requires before Cloud Run will create a mapping for a new
   domain — independent of this project, so it's safe to do any time, including before
   Step 1. Skipping it makes the mapping fail with a permission/verification error.
2. **Option A — Terraform** (recommended, matches this repo's IaC-first approach): set
   `custom_domain = "app.example.com"` in `terraform.tfvars`, `terraform apply`, then
   `terraform output custom_domain_dns_records`. The mapping's DNS target is only
   computed after creation, so this output may be empty on the same `apply` that creates
   the mapping — run `terraform apply` (or `terraform refresh`) a second time if so; this
   is expected, not a bug.
3. **Option B — by hand**:
   ```bash
   gcloud beta run domain-mappings create \
     --service="${SERVICE_NAME}" --domain="app.example.com" \
     --region="${REGION}" --project="${PROJECT_ID}"

   gcloud beta run domain-mappings describe \
     --domain="app.example.com" --region="${REGION}" --project="${PROJECT_ID}" \
     --format="table(status.resourceRecords)"
   ```
4. **DNS**: at your DNS provider, publish exactly what `resourceRecords` (or Terraform's
   `custom_domain_dns_records` output) returns — don't assume a specific target.
5. **Verify**: `curl -I https://app.example.com/` once DNS propagates and Cloud Run's
   managed TLS certificate provisions — this can take up to ~24h on a brand-new mapping,
   which is normal, not a failure. Don't use `/healthz` for this check: Cloud Run
   intentionally blocks external traffic to whatever path is configured as the
   container's startup probe (`/healthz` here — see `terraform/main.tf`'s
   `startup_probe` block), so it 404s from the outside even when the service is healthy.
   That's expected platform behavior, not a failure — it's why `/` is the right
   external check.
6. **Follow-up**: set `PUBLIC_BASE_URL=https://app.example.com` as a GitHub Actions
   repository variable (already consumed by `deploy.yml`) and in `terraform.tfvars`, so
   License Sync notification emails link to the custom domain instead of the `*.run.app`
   URL.

---

## Step 7: Sign In and Create Your First Tenant

1. Open the Cloud Run URL printed at the end of the workflow (or from the GCP Console).
2. Sign in with Google. First sign-in has no tenant yet, so you'll land on the tenant
   picker — click **Create a tenant**, name it (e.g. your own company, for testing), and
   you're made its `owner`.
3. Create an **environment** inside that tenant (e.g. "Production" or "Test"). It starts
   `status="onboarding"` and redirects to its **Settings** page — continue to Step 8.

---

## Step 8: Onboard a Tenant/Environment

Every environment needs its own service account in its own GCP project before its
module grid unlocks. This is the same flow whether you're a tenant onboarding yourself
or you're testing "tenant-zero" against the console's own project.

1. **In the tenant's own GCP project** (can be the console's own `${PROJECT_ID}` for
   quick testing — leave the fields in step 3 below blank to do that instead of running
   these commands):

   ```bash
   export TENANT_PROJECT_ID="the-tenant-s-gcp-project-id"
   export TENANT_SA_NAME="sa-ge-admin-console"
   export TENANT_SA_EMAIL="${TENANT_SA_NAME}@${TENANT_PROJECT_ID}.iam.gserviceaccount.com"

   gcloud iam service-accounts create "${TENANT_SA_NAME}" \
     --display-name="Gemini Enterprise Admin Console (managed)" \
     --project="${TENANT_PROJECT_ID}"
   ```

2. **Grant it roles for whichever modules this environment will use**, in the tenant's
   project:

   | Module | Role(s) on `${TENANT_SA_EMAIL}` | Also needs |
   | :--- | :--- | :--- |
   | License Sync | `roles/discoveryengine.admin` | A Domain-Wide Delegation entry (below) for Directory reads, and a Gemini Enterprise subscription in the project |
   | Health Check | `roles/iam.securityReviewer`, `roles/serviceusage.serviceUsageViewer` | — |
   | App URL Mapping | `roles/compute.loadBalancerAdmin` (project-scoped — see the note in [README → Dependencies](README.md#google-cloud-resources-central-apps-own-project)) | `compute.googleapis.com` enabled |

   ```bash
   gcloud projects add-iam-policy-binding "${TENANT_PROJECT_ID}" \
     --member="serviceAccount:${TENANT_SA_EMAIL}" --role="roles/discoveryengine.admin" --condition=None
   # ...repeat for the other roles this environment needs
   ```

3. **On the environment's Settings page** in the app (`/t/{tenant_id}/e/{environment_id}/settings`):
   - Enter **GCP Project ID** (`${TENANT_PROJECT_ID}`) and **Service Account Email**
     (`${TENANT_SA_EMAIL}`) and **Save**.
   - The page then shows a **Grant Access** command with the console's own runtime
     service account already filled in — run it (as someone with
     `roles/iam.serviceAccountAdmin` or `roles/owner` on the tenant's project):

     ```bash
     gcloud iam service-accounts add-iam-policy-binding ${TENANT_SA_EMAIL} \
       --member="serviceAccount:${SA_EMAIL}" \
       --role="roles/iam.serviceAccountTokenCreator"
     ```

     This is the **only** standing grant the tenant makes to the console — it lets the
     console impersonate this one service account, in this one project, and nothing
     else. Revoking it immediately cuts the console off from that environment.
   - Click **Test Connection**. It should report success once the binding above has
     propagated (usually seconds; occasionally a couple of minutes).
   - Click **Finish Setup**. This re-verifies the connection and flips the environment
     to `status="active"`, unlocking its module grid.
   - Leave both fields blank instead to run this environment as the console's own
     identity (tenant-zero) — **Finish Setup** then skips the connection check.

4. **If this environment will use License Sync**, also authorize Domain-Wide Delegation
   for `${TENANT_SA_EMAIL}` — see [License Sync: Domain-Wide Delegation](#license-sync-domain-wide-delegation)
   below.

5. **Enable the modules** this environment needs from its module grid (a toggle per
   module — no redeploy).

---

## License Sync: Domain-Wide Delegation

License Sync reads Google Groups via the Workspace Admin SDK Directory API, which needs
Domain-Wide Delegation authorized for the **environment's own service account**
(`${TENANT_SA_EMAIL}` from Step 8) — not the console's own service account.

1. Get the service account's numeric client ID:

   ```bash
   gcloud iam service-accounts describe "${TENANT_SA_EMAIL}" \
     --project="${TENANT_PROJECT_ID}" --format="value(uniqueId)"
   ```

2. Sign in to the [Google Admin Console](https://admin.google.com) for the tenant's
   Workspace domain as a **Super Administrator**.
3. **Security → Access and data control → API controls → Domain-wide delegation →
   Manage Domain Wide Delegation → Add new**.
4. **Client ID**: the numeric ID from step 1.
5. **OAuth Scopes** (comma-delimited):
   ```text
   https://www.googleapis.com/auth/admin.directory.group.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/gmail.send
   ```
   `gmail.send` is only needed for run-notification emails; omit it otherwise. Gemini
   Enterprise license management does **not** use DWD — it's a direct, impersonated
   Discovery Engine call, no `apps.licensing` scope required.
6. Click **Authorize**.
7. On the License Sync module's **Settings** page, set **Delegated Admin Email** to a
   real, active, licensed admin user in that Workspace tenant, pick the **Gemini
   Enterprise License Subscription**, and **Save**.

> `DELEGATED_ADMIN_EMAIL` **must resolve to an existing user**. A made-up address fails
> at sync time with `invalid_grant: Invalid email or User ID`.

---

## Scheduled Sync

Scheduled License Sync runs execute as a **Cloud Run job** (`${SYNC_JOB}`), triggered by
the **Cloud Scheduler** job `${SCHEDULER_JOB}` through the Cloud Run Admin API. This
path does not touch the web service or a browser session.

**Current limitation**: the job syncs exactly **one** designated tenant/environment,
named by `SCHEDULED_SYNC_TENANT_ID` and `SCHEDULED_SYNC_ENVIRONMENT_ID` (see
`app/job_runner.py`'s module docstring) — a per-environment scheduling fan-out is
tracked but not yet built. The job exits `1` on every run until both variables are set.
Other environments can still sync manually via **Run Sync Now** on the License Sync
dashboard; only the *automatic* cron path is limited to one environment today.

Set those two repository variables to the tenant/environment you want scheduled, then
wire the scheduler → job trigger (one-time; handled by `terraform apply`, or by hand):

```bash
SCHED_SA="sa-scheduler-invoker@${PROJECT_ID}.iam.gserviceaccount.com"   # create if absent

# 1. Let the scheduler SA execute the job (nothing else)
gcloud run jobs add-iam-policy-binding "${SYNC_JOB}" \
  --region="${REGION}" --project="${PROJECT_ID}" \
  --member="serviceAccount:${SCHED_SA}" --role="roles/run.invoker"

# 2. Point the Cloud Scheduler job at the job's :run endpoint (OAuth, not OIDC)
gcloud scheduler jobs update http "${SCHEDULER_JOB}" \
  --location="${REGION}" --project="${PROJECT_ID}" \
  --http-method=POST \
  --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/${SYNC_JOB}:run" \
  --oauth-service-account-email="${SCHED_SA}" \
  --update-headers="Content-Type=application/json" \
  --clear-body

# 3. Verify
gcloud scheduler jobs run "${SCHEDULER_JOB}" --location="${REGION}" --project="${PROJECT_ID}"
gcloud run jobs executions list --job="${SYNC_JOB}" --region="${REGION}" --project="${PROJECT_ID}"
```

---

## Run Notifications (optional, License Sync)

On the License Sync module's **Sync Schedule** page, under **Run Notifications**:

- Enter one or more recipient email addresses (comma- or newline-separated).
- Tick **Alert on all activity** to be emailed after every run; leave it unticked to be
  emailed only for **failed** and **partial-success** runs.

Requirements:

- `gmail.send` in that environment's DWD scopes (see
  [License Sync: Domain-Wide Delegation](#license-sync-domain-wide-delegation)).
- The sender mailbox is the delegated admin by default; override per-environment on the
  Settings page.
- Links use `PUBLIC_BASE_URL` if set on the console, otherwise the `*.run.app` URL the
  app last saw serving web traffic. **Set `PUBLIC_BASE_URL` explicitly if you front the
  service with a custom domain**.

Sending failures are logged (`gemini_provisioner.notifications`) and recorded on the
run, but never fail the sync itself.

---

## Security Model

- **Authentication**: Firebase Authentication, not IAP. The browser runs
  `signInWithPopup(new GoogleAuthProvider())` (see `app/templates/sign_in.html`), POSTs
  the resulting ID token to `POST /auth/session`, and the backend
  (`app/core/firebase_auth.py`) verifies it and mints an `HttpOnly` session cookie
  (`ge_session`, 5-day lifetime, see `app/auth_routes.py`).
- **Authorization**: every tenant- and environment-scoped route requires the signed-in
  user to be a Firestore-recorded member of that tenant (`app/core/session_auth.py`).
  Tenant-management actions additionally require the `owner` or `admin` role. A disabled
  module's page routes redirect to the module grid with a toast; its API routes 403.
- **The web service is intentionally public** (`--allow-unauthenticated`,
  `allUsers` invoker) — this is a self-serve, multi-tenant SaaS surface, not a
  single pre-authorized organization, so there's no network-level gate to turn on. See
  the comment on `google_cloud_run_v2_service` in `terraform/main.tf`. All access control
  happens in-app, per the two bullets above.
- **Cross-project trust is narrow and revocable, not a standing credential.** The
  console never holds broad access into a tenant's project. It only ever impersonates
  the one service account that environment registered, for as long as that environment
  leaves `roles/iam.serviceAccountTokenCreator` granted on it (see
  [Step 8](#step-8-onboard-a-tenantenvironment) and `app/core/tenant_credentials.py`).
  Revoking that single binding immediately cuts the console off from that environment —
  no other coordination needed.
- **Scheduled sync** does not go through the web service or a browser session — Cloud
  Scheduler executes the `${SYNC_JOB}` Cloud Run job directly (see
  [Scheduled Sync](#scheduled-sync)).
- `GET /healthz` is always open (Cloud Run probes). Impersonated/DWD credentials are
  built server-side per request and never exposed to the browser.

### Still worth doing

- The console's own runtime service account is privileged within its own project
  (deploys Cloud Run, self-impersonates). Split CI onto a separate identity per the note
  in [Required Privileges](#required-privileges) if you don't need in-place deploys.
- Review each tenant's granted role set periodically — the **Health Check** module
  (once enabled for that environment) audits IAM bindings, enabled APIs, and
  impersonation connectivity for exactly this purpose.

---

## Troubleshooting

### Test Connection (environment settings) fails with an impersonation/permission error

The console couldn't act as the environment's configured service account. Check, in
order:

1. `roles/iam.serviceAccountTokenCreator` is granted **on the environment's service
   account** (`${TENANT_SA_EMAIL}`), with member = the console's own runtime service
   account (`${SA_EMAIL}`) — this is the "Grant Access" command shown on the Settings
   page. Cross-project IAM bindings can take a few minutes to propagate.
2. `iamcredentials.googleapis.com` is enabled in the **console's own** project.
3. The **GCP Project ID** and **Service Account Email** fields on the environment's
   Settings page are saved and match exactly.
4. `roles/serviceusage.serviceUsageViewer` (or broader) is granted to
   `${TENANT_SA_EMAIL}` in its own project — Test Connection's underlying check is a
   trivial Service Usage API call.

### License Sync's own Test Connection returns `API Error (404): Domain not found.`

The service reached Google but called the Directory API without a valid Workspace
subject. Check:

1. Domain-Wide Delegation (see
   [License Sync: Domain-Wide Delegation](#license-sync-domain-wide-delegation)) is
   authorized for **this environment's service account's** numeric client ID, not the
   console's.
2. The environment's **Delegated Admin Email** is a real, active, super-admin user on
   that Workspace domain.
3. The environment's Settings page connection test (above) passes first — DWD failures
   downstream of a broken impersonation path surface the same way.

### License Sync's Test Connection returns `invalid_grant: Invalid email or User ID`

Impersonation works, but **Delegated Admin Email** is not a real user in the tenant. Set
it on the module's Settings page to an active, licensed admin account and save.

### License Sync's Test Connection returns `403` / `unauthorized_client`

The DWD entry is missing, has the wrong client ID, or lacks one of the three scopes.
Re-check it against the numeric client ID for `${TENANT_SA_EMAIL}` (or the console's own
service account, if this environment is running as tenant-zero).

### Sign-in fails or the sign-in button does nothing

`FIREBASE_API_KEY` and/or `FIREBASE_PROJECT_ID` aren't set on the Cloud Run service, or
Google sign-in isn't enabled in the Firebase console (see
[Step 4](#step-4-set-up-firebase-authentication)). Check the browser console for a
Firebase SDK error.

### A new environment's module grid keeps redirecting back to Settings

Expected — that's the onboarding gate. It stays on `/settings` until **Finish Setup**
succeeds (see [Step 8](#step-8-onboard-a-tenantenvironment)).
