# Worked Example: the `ge-hoffhouse` Deployment

A concrete, filled-in version of [`setup_instructions.md`](setup_instructions.md).
Nothing here is required — it is one real set of values so the placeholders in the
guide are easy to map. Substitute your own everywhere.

## 0. Values

```bash
export PROJECT_ID="ge-hoffhouse"
export REGION="us-central1"
export SA_NAME="sa-gemini-provisioner"
export ARTIFACT_REPO="gemini-provisioner-docker"
export SERVICE_NAME="gemini-license-provisioner"
export SCHEDULER_JOB="gemini-license-sync-job"

export GITHUB_REPO="hoffshouse/gemini-license-provisioner"

export WORKSPACE_DOMAIN="hoffshouse.com"
export DELEGATED_ADMIN_EMAIL="david@hoffshouse.com"

export SA_EMAIL="sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com"
```

## GitHub configuration

**Secrets** (Settings → Secrets and variables → Actions → Secrets):

| Secret | Value |
| :--- | :--- |
| `WIF_PROVIDER` | `projects/1016465712043/locations/global/workloadIdentityPools/github-actions-pool/providers/github-provider` |
| `WIF_SERVICE_ACCOUNT` | `sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com` |

**Variables** (same screen, Variables tab):

| Variable | Value |
| :--- | :--- |
| `GCP_PROJECT_ID` | `ge-hoffhouse` |
| `GCP_REGION` | `us-central1` |

(`CLOUD_RUN_SERVICE`, `ARTIFACT_REPO`, `CLOUD_SCHEDULER_JOB` left unset — the workflow
defaults match the values above. `DELEGATED_ADMIN_EMAIL` left unset — it is set on the
app's Settings page instead.)

## Google Workspace Domain-Wide Delegation

Admin Console → Security → Access and data control → API controls →
Domain-wide delegation → Add new:

| Field | Value |
| :--- | :--- |
| Client ID | `112103280116941655961` (the service account's numeric `uniqueId`) |
| OAuth scopes | `https://www.googleapis.com/auth/admin.directory.group.readonly,https://www.googleapis.com/auth/admin.directory.user.readonly,https://www.googleapis.com/auth/apps.licensing,https://www.googleapis.com/auth/gmail.send` |

(`gmail.send` is only needed for run-notification emails.)

Delegated admin impersonated at runtime: `david@hoffshouse.com` (active super admin).

## Result

- Cloud Run service: `https://gemini-license-provisioner-uzzke5h76a-uc.a.run.app`
- Settings / Test Connection: `…/settings`
- Runtime + CI/CD service account: `sa-gemini-provisioner@ge-hoffhouse.iam.gserviceaccount.com`
  with `datastore.user`, `cloudscheduler.admin`, `logging.logWriter`, `run.admin`,
  `artifactregistry.admin`, `iam.serviceAccountUser`, and
  `iam.serviceAccountTokenCreator` on itself.

> Access is currently public (`--allow-unauthenticated`). See
> [setup_instructions.md → Security Model](setup_instructions.md#security-model)
> before treating this as production.
