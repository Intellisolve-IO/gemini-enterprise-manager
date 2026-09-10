# Setup Instructions: Google Workspace Gemini Enterprise License Provisioner

This guide details step-by-step instructions to configure Google Cloud Platform (`ge-hoffhouse`), Google Workspace Domain-Wide Delegation (DWD), Workload Identity Federation (WIF), and GitHub Actions.

---

## Prerequisites

1. **GCP Project**: `ge-hoffhouse` with Owner or Project IAM Admin access.
2. **Google Workspace**: Super Administrator access to [Google Admin Console](https://admin.google.com).
3. **GitHub**: A new GitHub repository (e.g. `https://github.com/david-hoff/gemini-license-provisioner`).

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

Create the dedicated application Service Account:

```bash
# Create Service Account
gcloud iam service-accounts create sa-gemini-provisioner \
  --display-name="Gemini License Provisioner Service Account" \
  --project="ge-hoffhouse"

# Grant Firestore access (roles/datastore.user)
gcloud projects add-iam-policy-binding ge-hoffhouse \
  --member="serviceAccount:sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com" \
  --role="roles/datastore.user"

# Grant Cloud Scheduler Admin (roles/cloudscheduler.admin)
gcloud projects add-iam-policy-binding ge-hoffhouse \
  --member="serviceAccount:sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com" \
  --role="roles/cloudscheduler.admin"

# Grant Cloud Logging Writer
gcloud projects add-iam-policy-binding ge-hoffhouse \
  --member="serviceAccount:sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com" \
  --role="roles/logging.logWriter"

# Allow the Service Account to sign JWTs as itself (keyless Domain-Wide Delegation).
# Without this, the deployed service authenticates as the bare Service Account and the
# Directory API returns "404: Domain not found" on the Test Connection button.
gcloud iam service-accounts add-iam-policy-binding \
  sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com \
  --member="serviceAccount:sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com" \
  --role="roles/iam.serviceAccountTokenCreator" \
  --project="ge-hoffhouse"
```

> The Cloud Run service must also receive the `RUNTIME_SERVICE_ACCOUNT_EMAIL`
> environment variable (set to `sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com`).
> The Terraform config and the GitHub Actions workflow in this repo already set it; if you
> deploy `gcloud run deploy` by hand, add it to `--set-env-vars`.

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
cd /Users/david.hoff/.gemini/antigravity/scratch/gemini-license-provisioner
bash scripts/setup_wif.sh <YOUR-GITHUB-USERNAME>/gemini-license-provisioner
```

The script will output two values. Add them to your GitHub repository under **Settings** &gt; **Secrets and variables** &gt; **Actions** &gt; **Repository secrets**:

| Secret Name | Value Example |
| :--- | :--- |
| `WIF_PROVIDER` | `projects/1234567890/locations/global/workloadIdentityPools/github-actions-pool/providers/github-provider` |
| `WIF_SERVICE_ACCOUNT` | `sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com` |

---

## Step 6: Create GitHub Repository & Push Code

Initialize your new repository in GitHub:

```bash
cd /Users/david.hoff/.gemini/antigravity/scratch/gemini-license-provisioner

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
   - Verify the **Delegated Admin Email** (e.g. `admin@yourdomain.com`).
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
