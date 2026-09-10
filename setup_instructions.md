# Setup Instructions: Google Workspace Gemini Enterprise License Provisioner

This guide details step-by-step instructions to configure Google Cloud Platform (`ge-hoffhouse`), Google Workspace Domain-Wide Delegation (DWD), Workload Identity Federation (WIF), and GitHub Actions.

---

## Prerequisites

1. **GCP Project**: `ge-hoffhouse`, with billing enabled and an operator identity that holds the roles listed under [Required Privileges](#required-privileges).
2. **Google Workspace / Cloud Identity**: a tenant on the domain you intend to license (for example `your-domain.com`), plus a **Super Admin** account in that tenant to configure Domain-Wide Delegation.
3. **A dedicated delegated-admin user** in that Workspace tenant for the service to impersonate — a real, licensed account such as `workspace-admin@your-domain.com`. It must not be a made-up address; the sync fails with `invalid_grant: Invalid email or User ID` if the user does not exist.
4. **GitHub**: a repository for the code (`<YOUR-GITHUB-USERNAME>/gemini-license-provisioner`) with permission to add Actions secrets.
5. **Local tooling**: `gcloud` CLI authenticated to `ge-hoffhouse` (`gcloud auth login && gcloud config set project ge-hoffhouse`), and `git`.

---

## Required Privileges

### Google Cloud — operator (the person running Steps 1–5)

Granted on project `ge-hoffhouse`. Either `roles/owner`, or this least-privilege set:

| Role | Needed for |
| :--- | :--- |
| `roles/serviceusage.serviceUsageAdmin` | Enable APIs (Step 1) |
| `roles/datastore.owner` | Create the Firestore database (Step 2) |
| `roles/iam.serviceAccountAdmin` | Create the service account and set IAM policy **on** it (Steps 3, 5) |
| `roles/resourcemanager.projectIamAdmin` | Grant project roles to the service account (Steps 3, 5) |
| `roles/artifactregistry.admin` | Create the Docker repository (Terraform / first deploy) |
| `roles/run.admin` | Create the Cloud Run service (first deploy) |
| `roles/iam.serviceAccountUser` on `sa-gemini-provisioner@…` | Deploy Cloud Run "acting as" the runtime service account |
| `roles/cloudscheduler.admin` | Create the Cloud Scheduler job |
| `roles/iam.workloadIdentityPoolAdmin` | Create the WIF pool/provider (Step 5) |

### Google Cloud — `sa-gemini-provisioner` service account (runtime **and** CI/CD)

This one service account both runs the Cloud Run service and is the identity GitHub Actions impersonates to deploy. Steps 3 and 5 (`scripts/setup_wif.sh`) grant it:

| Role | Scope | Purpose |
| :--- | :--- | :--- |
| `roles/datastore.user` | project | Read/write config and sync history in Firestore |
| `roles/cloudscheduler.admin` | project | The **Sync Schedule** page edits the scheduler job at runtime |
| `roles/logging.logWriter` | project | Structured logs |
| `roles/iam.serviceAccountTokenCreator` | **on itself** | Sign JWTs for **keyless Domain-Wide Delegation** — without it, Test Connection returns `404: Domain not found` |
| `roles/run.admin` | project | GitHub Actions deploys new revisions |
| `roles/iam.serviceAccountUser` | on itself | GitHub Actions deploys Cloud Run as this account |
| `roles/artifactregistry.admin` | project | GitHub Actions pushes container images |

> For a stricter setup, split deployment onto a separate CI service account holding only `run.admin`, `artifactregistry.writer`, and `iam.serviceAccountUser` on the runtime account, and drop `run.admin` / `artifactregistry.admin` / `iam.serviceAccountUser` from `sa-gemini-provisioner`.

### Google Workspace

| Privilege | Held by | Needed for |
| :--- | :--- | :--- |
| **Super Admin** (one-time) | the admin doing Step 4 | Add the Domain-Wide Delegation entry in the Admin Console. DWD cannot be delegated to a custom admin role. |
| Admin roles: **Groups → Read**, **Users → Read**, and license management (Super Admin covers all three) | the delegated-admin user the app impersonates (`workspace-admin@your-domain.com`) | The app calls Admin SDK Directory (`admin.directory.group.readonly`, `admin.directory.user.readonly`) and Enterprise License Manager (`apps.licensing`) **as this user** |
| An assignable **Gemini Enterprise** SKU with available seats | the Workspace tenant | Licenses to hand out during sync |

The delegated-admin user must be **active** (not suspended) and licensed. A Super Admin account is the simplest choice; a custom admin role works only if it grants the Directory read and licensing privileges above.

---

## Step 1: Enable Required Google Cloud APIs

Run the following command using `gcloud` authenticated to `ge-hoffhouse`:

```bash
gcloud services enable \
  admin.googleapis.com \
  licensing.googleapis.com \
  cloudscheduler.googleapis.com \
  firestore.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  cloudbuild.googleapis.com \
  --project="ge-hoffhouse"
```

---

## Step 2: Initialize Cloud Firestore in Native Mode

If Cloud Firestore is not yet activated in `ge-hoffhouse`:

1. In the Google Cloud Console, navigate to **Firestore Studio**.
2. Click **Create Database**.
3. Select **Firestore Native mode**.
4. Choose region `us-central1` (or your preferred region) and name `(default)`.
5. Click **Create Database**.

---

## Step 3: Create Service Account & Grant GCP Roles

Create the dedicated `sa-gemini-provisioner` service account. It is the single
account that both **runs** the Cloud Run service and is impersonated by
**GitHub Actions** to deploy it; see [Required Privileges](#required-privileges)
for the rationale behind each role. `scripts/setup_wif.sh` (Step 5) applies the
same project-level bindings, so you can skip straight to Step 5 if you use it.

```bash
SA="sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com"

# Create Service Account
gcloud iam service-accounts create sa-gemini-provisioner \
  --display-name="Gemini License Provisioner Service Account" \
  --project="ge-hoffhouse"

# Project-level roles (runtime + CI/CD deploy)
for ROLE in \
  roles/datastore.user \
  roles/cloudscheduler.admin \
  roles/logging.logWriter \
  roles/run.admin \
  roles/artifactregistry.admin \
  roles/iam.serviceAccountUser
do
  gcloud projects add-iam-policy-binding ge-hoffhouse \
    --member="serviceAccount:${SA}" --role="${ROLE}" --condition=None
done

# Allow the Service Account to mint signed JWTs as itself (keyless Domain-Wide
# Delegation). Without this the deployed service authenticates as the bare
# Service Account and the Directory API returns "404: Domain not found" on the
# Test Connection button.
gcloud iam service-accounts add-iam-policy-binding "${SA}" \
  --member="serviceAccount:${SA}" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project="ge-hoffhouse"
```

> **Runtime environment variables.** The Cloud Run service needs:
> - `RUNTIME_SERVICE_ACCOUNT_EMAIL=sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com`
>   — required for keyless DWD.
> - `DELEGATED_ADMIN_EMAIL=workspace-admin@your-domain.com` — the delegated-admin
>   user to impersonate (can also be set later from the **Settings** page).
>
> The Terraform config and the GitHub Actions workflow set `RUNTIME_SERVICE_ACCOUNT_EMAIL`
> automatically; if you run `gcloud run deploy` by hand, pass both in `--set-env-vars`.

Retrieve the **Unique Numeric Client ID** of the Service Account (needed for Step 4):

```bash
gcloud iam service-accounts describe sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com \
  --project="ge-hoffhouse" \
  --format="value(uniqueId)"
```
*Note down this numeric ID (e.g., `108392019482019482019`).*

---

## Step 4: Authorize Domain-Wide Delegation (DWD) in Google Workspace

To allow the Service Account to read Google Groups and assign Gemini licenses to users in your domain:

1. Log into the [Google Admin Console](https://admin.google.com) as a Super Administrator.
2. In the left navigation menu, go to:
   **Security** &gt; **Access and data control** &gt; **API controls**.
3. Under **Domain-wide delegation**, click **Manage Domain Wide Delegation**.
4. Click **Add new**.
5. In the **Client ID** field, paste the **Unique Numeric Client ID** retrieved in Step 3.
6. In the **OAuth Scopes** field, paste the following comma-delimited scopes:
   ```text
   https://www.googleapis.com/auth/admin.directory.group.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/apps.licensing
   ```
7. Click **Authorize**.

---

## Step 5: Configure Workload Identity Federation (WIF) for GitHub Actions

WIF eliminates the need to export static private keys to GitHub Secrets.

Run the provided setup script with your new GitHub repository name:

```bash
cd gemini-license-provisioner
bash scripts/setup_wif.sh <YOUR-GITHUB-USERNAME>/gemini-license-provisioner
```

`setup_wif.sh` creates the service account (if missing), applies the project-level
roles from Step 3, grants `roles/iam.serviceAccountTokenCreator` on the account to
itself, and creates the Workload Identity pool/provider.

The script will output two values. Add them to your GitHub repository under **Settings** &gt; **Secrets and variables** &gt; **Actions** &gt; **Repository secrets**:

| Secret Name | Value Example |
| :--- | :--- |
| `WIF_PROVIDER` | `projects/1234567890/locations/global/workloadIdentityPools/github-actions-pool/providers/github-provider` |
| `WIF_SERVICE_ACCOUNT` | `sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com` |

---

## Step 6: Create GitHub Repository & Push Code

Initialize your new repository in GitHub:

```bash
cd gemini-license-provisioner

git init
git add .
git commit -m "Initial commit: Gemini Enterprise automated license provisioner"
git branch -M main

# Add your GitHub remote URL
git remote add origin https://github.com/<YOUR-GITHUB-USERNAME>/gemini-license-provisioner.git
git push -u origin main
```

Upon pushing to `main`, the GitHub Actions workflow (`.github/workflows/deploy.yml`) will automatically trigger, build the Docker container, push to Artifact Registry, and deploy the service to Cloud Run.

---

## Step 7: Initial Configuration in Admin Dashboard

Once deployed:

1. Open the public Cloud Run URL printed in GitHub Actions or GCP Console.
2. Navigate to **Settings &amp; Test**:
   - Set the **Delegated Admin Email** to your real delegated-admin user
     (e.g. `workspace-admin@your-domain.com`) and **Save**.
   - Confirm **Product ID** (`Google-Apps` or `101047`) and **SKU ID** (`101031` or `1010470001`).
   - Click **Test Connection** to confirm DWD connectivity.
3. Navigate to **Monitored Groups**:
   - Check the Google Groups you want to track.
   - Click **Save Monitored Groups**.
4. Navigate to **Sync Schedule**:
   - Confirm or update the cron frequency (e.g. Daily at 2 AM or Hourly).
   - Click **Update Cloud Scheduler**.
5. Click **Run Sync Now** to execute your first automated provisioning cycle!

---

## Troubleshooting

### Test Connection returns `API Error (404): Domain not found.`

The service reached Google but called the Directory API as the bare runtime Service
Account instead of impersonating a Workspace admin. Check, in order:

1. **`roles/iam.serviceAccountTokenCreator` on the SA itself** (Step 3, last command).
2. **`RUNTIME_SERVICE_ACCOUNT_EMAIL`** is set on the Cloud Run service and matches the
   attached Service Account.
3. **`iamcredentials.googleapis.com`** is enabled (Step 1).
4. The **Delegated Admin Email** domain is a real Google Workspace / Cloud Identity
   domain, and that user is an **active super administrator**.
5. The Domain-Wide Delegation entry (Step 4) uses the SA's **numeric client ID** with
   all three scopes.

Once impersonation works but privileges are wrong, the error changes to `401
unauthorized_client` or `403` - that points at Step 4 or the admin user's role.

### Test Connection returns `invalid_grant: Invalid email or User ID`

Impersonation is working, but the **Delegated Admin Email** is not a real user in
the Workspace tenant. Set it (on the **Settings** page or via `DELEGATED_ADMIN_EMAIL`)
to an active, licensed admin account on your actual domain and save.

### Test Connection returns `403` / `unauthorized_client`

The Domain-Wide Delegation entry (Step 4) is missing, uses the wrong client ID, or
lacks one of the three scopes. Re-check it against the SA's numeric client ID from
Step 3.
