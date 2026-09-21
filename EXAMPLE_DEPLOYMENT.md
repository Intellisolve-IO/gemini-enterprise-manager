# Worked Example

[`setup_instructions.md`](setup_instructions.md) with every placeholder filled in, using
a **fictional** operator (`acme-ge-console` — the console's own project) and a
**fictional** onboarding tenant (`Beta Corp`, project `betacorp-prod`,
domain `beta.example`). Nothing here is real or required — substitute your own values
throughout.

## Part A — deploying the console

### Values

```bash
export PROJECT_ID="acme-ge-console"
export REGION="us-central1"
export SA_NAME="sa-gemini-provisioner"
export ARTIFACT_REPO="gemini-provisioner-docker"
export SERVICE_NAME="gemini-license-provisioner"
export SCHEDULER_JOB="gemini-license-sync-job"
export SYNC_JOB="gemini-license-sync-runner"

export GITHUB_REPO="acme-corp/gemini-enterprise-manager"

export SA_EMAIL="sa-gemini-provisioner@acme-ge-console.iam.gserviceaccount.com"
```

Project number for this example: `750123456789`.

### Firebase (Step 4)

- Firebase project: `acme-ge-console` (same GCP project).
- Google sign-in enabled under Authentication.
- Web app registered → **Web API key** `AIzaSyD-exampleFirebaseWebApiKey12345`,
  **Project ID** `acme-ge-console`.

### GitHub configuration (Step 5)

**Secrets** (Settings → Secrets and variables → Actions → Secrets):

| Secret | Value |
| :--- | :--- |
| `WIF_PROVIDER` | `projects/750123456789/locations/global/workloadIdentityPools/github-actions-pool/providers/github-provider` |
| `WIF_SERVICE_ACCOUNT` | `sa-gemini-provisioner@acme-ge-console.iam.gserviceaccount.com` |

**Variables** (same screen, Variables tab):

| Variable | Value |
| :--- | :--- |
| `GCP_PROJECT_ID` | `acme-ge-console` |
| `GCP_REGION` | `us-central1` |
| `FIREBASE_API_KEY` | `AIzaSyD-exampleFirebaseWebApiKey12345` |
| `FIREBASE_PROJECT_ID` | `acme-ge-console` |

`CLOUD_RUN_SERVICE`, `ARTIFACT_REPO`, `CLOUD_SCHEDULER_JOB`, `CLOUD_RUN_JOB` are left
unset here because the workflow defaults already match.

### Result

- Cloud Run service: `https://gemini-license-provisioner-abcde12345-uc.a.run.app`
- Console's own runtime + CI/CD service account
  `sa-gemini-provisioner@acme-ge-console.iam.gserviceaccount.com` with `datastore.user`,
  `discoveryengine.admin`, `cloudscheduler.admin`, `logging.logWriter`,
  `iam.securityReviewer`, `serviceusage.serviceUsageViewer`, `run.admin`,
  `artifactregistry.admin`, `iam.serviceAccountUser`, and
  `iam.serviceAccountTokenCreator` on itself.

### Custom Domain

Acme owns `acme.example` and wants the console at `licensing.acme.example` instead of
the `*.run.app` URL. After verifying `acme.example` in Search Console (once, for the
Google account behind `acme-ge-console`):

```hcl
# terraform.tfvars
custom_domain = "licensing.acme.example"
```

```bash
terraform apply
terraform output custom_domain_dns_records
# [{ type = "CNAME", name = "licensing.acme.example.", rrdata = "ghs.googlehosted.com." }]
```

Acme publishes that CNAME at their DNS provider, waits for propagation and the managed
TLS cert (up to ~24h), then confirms with `curl -I https://licensing.acme.example/healthz`.
Repository variable `PUBLIC_BASE_URL=https://licensing.acme.example` is set afterward so
notification emails link to the custom domain.

---

## Part B — onboarding a tenant

Signed in at the URL above as `alex@acme-corp.example` (the operator, testing the
console with a real customer). Created tenant **"Beta Corp"** → became its `owner` →
created environment **"Production"**, which lands on
`.../settings` with `status="onboarding"`.

### Values

```bash
export TENANT_PROJECT_ID="betacorp-prod"
export TENANT_SA_NAME="sa-ge-admin-console"
export TENANT_SA_EMAIL="sa-ge-admin-console@betacorp-prod.iam.gserviceaccount.com"
export WORKSPACE_DOMAIN="beta.example"
```

### GCP setup in Beta Corp's own project (Step 8, points 1–2)

```bash
gcloud iam service-accounts create "${TENANT_SA_NAME}" \
  --display-name="Gemini Enterprise Admin Console (managed)" \
  --project="${TENANT_PROJECT_ID}"

# Beta Corp is enabling License Sync and Health Check for this environment.
for ROLE in \
  roles/discoveryengine.admin \
  roles/iam.securityReviewer \
  roles/serviceusage.serviceUsageViewer
do
  gcloud projects add-iam-policy-binding "${TENANT_PROJECT_ID}" \
    --member="serviceAccount:${TENANT_SA_EMAIL}" --role="${ROLE}" --condition=None
done
```

### Environment Settings page (Step 8, point 3)

- **GCP Project ID**: `betacorp-prod`
- **Service Account Email**: `sa-ge-admin-console@betacorp-prod.iam.gserviceaccount.com`
- Saved, then ran the **Grant Access** command the page generated (as a Beta Corp GCP
  admin):

  ```bash
  gcloud iam service-accounts add-iam-policy-binding sa-ge-admin-console@betacorp-prod.iam.gserviceaccount.com \
    --member="serviceAccount:sa-gemini-provisioner@acme-ge-console.iam.gserviceaccount.com" \
    --role="roles/iam.serviceAccountTokenCreator"
  ```

- **Test Connection** → success.
- **Finish Setup** → environment `status` flips to `active`; module grid unlocks.

### License Sync's Domain-Wide Delegation

```bash
gcloud iam service-accounts describe "${TENANT_SA_EMAIL}" \
  --project="${TENANT_PROJECT_ID}" --format="value(uniqueId)"
# → 109876543210987654321
```

Admin Console (`beta.example`, signed in as a Beta Corp Super Admin) → Security → Access
and data control → API controls → Domain-wide delegation → Add new:

| Field | Value |
| :--- | :--- |
| Client ID | `109876543210987654321` |
| OAuth scopes | `https://www.googleapis.com/auth/admin.directory.group.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/gmail.send` |

On the License Sync module's own **Settings** page: **Delegated Admin Email**
`ws-provisioner@beta.example` (real, active, licensed admin), license subscription
`free_trial_gemini`, saved.

### Result

- Beta Corp's Production environment is `active`, with License Sync and Health Check
  enabled.
- Beta Corp granted exactly one standing binding to the console:
  `iam.serviceAccountTokenCreator` on their own `sa-ge-admin-console`, scoped to their
  own project — revocable at any time from their own IAM console, no coordination with
  the console operator needed.
- Scheduled sync for this environment requires the operator to set repository variables
  `SCHEDULED_SYNC_TENANT_ID` / `SCHEDULED_SYNC_ENVIRONMENT_ID` to Beta Corp's
  tenant/environment ids (visible in the environment's URL) and redeploy — see
  [setup_instructions.md → Scheduled Sync](setup_instructions.md#scheduled-sync) for why
  this is currently a single slot, not per-tenant.
