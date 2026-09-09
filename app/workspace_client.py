import json
import logging
import os
from typing import Dict, Any, List, Optional, Tuple

import google.auth
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from app.config import settings

logger = logging.getLogger("gemini_provisioner.workspace")

SCOPES = [
    "https://www.googleapis.com/auth/admin.directory.group.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
    "https://www.googleapis.com/auth/apps.licensing",
]


class WorkspaceClient:
    """Client for interacting with Google Workspace Admin Directory and Licensing APIs."""

    def __init__(self, delegated_admin_email: Optional[str] = None):
        self.delegated_admin_email = delegated_admin_email or settings.DELEGATED_ADMIN_EMAIL

    def get_credentials(self, subject_email: Optional[str] = None):
        """Build Google OAuth2 credentials with Domain-Wide Delegation (DWD)."""
        subject = subject_email or self.delegated_admin_email

        # 1. Check if raw JSON string is provided in env var (e.g. from Secret Manager)
        if settings.SERVICE_ACCOUNT_KEY_JSON:
            try:
                key_info = json.loads(settings.SERVICE_ACCOUNT_KEY_JSON)
                creds = service_account.Credentials.from_service_account_info(
                    key_info, scopes=SCOPES
                )
                return creds.with_subject(subject) if subject else creds
            except Exception as e:
                logger.error("Failed to parse SERVICE_ACCOUNT_KEY_JSON: %s", e)
                raise

        # 2. Check if file path is provided in env var
        if settings.SERVICE_ACCOUNT_KEY_PATH and os.path.exists(settings.SERVICE_ACCOUNT_KEY_PATH):
            try:
                creds = service_account.Credentials.from_service_account_file(
                    settings.SERVICE_ACCOUNT_KEY_PATH, scopes=SCOPES
                )
                return creds.with_subject(subject) if subject else creds
            except Exception as e:
                logger.error("Failed to load SERVICE_ACCOUNT_KEY_PATH: %s", e)
                raise

        # 3. Fallback to Application Default Credentials (ADC)
        # Note: Domain-Wide Delegation in Google Workspace requires a service account credential
        # with a subject claim. On GCP (Cloud Run), either mount the SA key via Secret Manager
        # or use IAM Credentials impersonation.
        try:
            creds, _ = google.auth.default(scopes=SCOPES)
            if hasattr(creds, "with_subject") and subject:
                return creds.with_subject(subject)
            return creds
        except Exception as e:
            logger.error("Could not obtain default credentials: %s", e)
            raise RuntimeError(
                "No valid Google credentials found. Please set SERVICE_ACCOUNT_KEY_JSON or "
                "SERVICE_ACCOUNT_KEY_PATH with Domain-Wide Delegation authority."
            ) from e

    def get_directory_service(self, subject_email: Optional[str] = None):
        """Construct Google Workspace Admin SDK Directory API client."""
        creds = self.get_credentials(subject_email)
        return build("admin", "directory_v1", credentials=creds, cache_discovery=False)

    def get_licensing_service(self, subject_email: Optional[str] = None):
        """Construct Google Workspace Enterprise License Manager API client."""
        creds = self.get_credentials(subject_email)
        return build("licensing", "v1", credentials=creds, cache_discovery=False)

    def test_dwd_connection(self, subject_email: Optional[str] = None) -> Dict[str, Any]:
        """Perform a live connectivity and permission test against the Directory API.
        
        Verifies that:
        1. Service Account credentials can be parsed.
        2. DWD impersonation succeeds with the given admin email.
        3. Scopes are granted in Google Workspace Admin Console.
        """
        subject = subject_email or self.delegated_admin_email
        try:
            service = self.get_directory_service(subject)
            # Query groups with maxResults=1 to verify group.readonly scope & DWD
            result = service.groups().list(customer="my_customer", maxResults=1).execute()
            groups_found = len(result.get("groups", []))
            return {
                "success": True,
                "message": f"Successfully connected to Admin SDK Directory API. DWD authenticated as {subject}.",
                "subject": subject,
                "groups_sample_count": groups_found,
            }
        except HttpError as err:
            logger.error("HTTP error during DWD connection test: %s", err)
            status_code = err.resp.status
            reason = err.reason
            guidance = ""
            if status_code == 403 or "unauthorized_client" in str(err).lower():
                guidance = (
                    "Client not authorized. Ensure the Service Account Client ID is added to "
                    "Google Admin Console > Security > Access and data control > API controls > Domain-wide delegation "
                    "with scope 'https://www.googleapis.com/auth/admin.directory.group.readonly'."
                )
            elif status_code == 400:
                guidance = f"Bad request. Ensure '{subject}' is an active admin user in the domain."
            
            return {
                "success": False,
                "status_code": status_code,
                "message": f"API Error ({status_code}): {reason}",
                "guidance": guidance,
                "subject": subject,
            }
        except Exception as ex:
            logger.error("Unexpected error testing DWD connection: %s", ex)
            return {
                "success": False,
                "message": f"Connection failed: {str(ex)}",
                "subject": subject,
                "guidance": "Check that the service account key or environment variables are set correctly."
            }

    def list_domain_groups(self, subject_email: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all Google Groups in the Workspace domain."""
        service = self.get_directory_service(subject_email)
        groups = []
        page_token = None

        while True:
            response = service.groups().list(
                customer="my_customer",
                maxResults=200,
                pageToken=page_token
            ).execute()
            
            for item in response.get("groups", []):
                groups.append({
                    "id": item.get("id"),
                    "email": item.get("email"),
                    "name": item.get("name"),
                    "description": item.get("description", ""),
                    "directMembersCount": item.get("directMembersCount", 0)
                })
                
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        # Sort alphabetically by name
        groups.sort(key=lambda g: g.get("name", "").lower())
        return groups

    def list_direct_group_members(self, group_key: str, subject_email: Optional[str] = None) -> List[Dict[str, Any]]:
        """List direct members of a Google Group (no recursive traversal)."""
        service = self.get_directory_service(subject_email)
        members = []
        page_token = None

        while True:
            try:
                response = service.members().list(
                    groupKey=group_key,
                    maxResults=200,
                    pageToken=page_token
                ).execute()
            except HttpError as e:
                logger.error("Failed to list members for group %s: %s", group_key, e)
                raise

            for m in response.get("members", []):
                members.append({
                    "id": m.get("id"),
                    "email": m.get("email"),
                    "type": m.get("type"),    # "USER", "GROUP", "CUSTOMER"
                    "role": m.get("role"),    # "MEMBER", "MANAGER", "OWNER"
                    "status": m.get("status") # "ACTIVE", "SUSPENDED"
                })

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return members

    def check_license(
        self,
        product_id: str,
        sku_id: str,
        user_email: str,
        subject_email: Optional[str] = None
    ) -> bool:
        """Check if a user already holds the specified license SKU.
        
        Returns:
            True if the user has the license (HTTP 200).
            False if the user does not have the license (HTTP 404).
        """
        service = self.get_licensing_service(subject_email)
        try:
            assignment = service.licenseAssignments().get(
                productId=product_id,
                skuId=sku_id,
                userId=user_email
            ).execute()
            return bool(assignment)
        except HttpError as err:
            if err.resp.status == 404:
                return False
            logger.error("Error checking license for %s (Product: %s, SKU: %s): %s", user_email, product_id, sku_id, err)
            raise

    def assign_license(
        self,
        product_id: str,
        sku_id: str,
        user_email: str,
        subject_email: Optional[str] = None
    ) -> Tuple[bool, Optional[str]]:
        """Assign the specified product SKU license to a user.
        
        Returns:
            (True, None) on success.
            (False, error_message) on failure.
        """
        service = self.get_licensing_service(subject_email)
        try:
            body = {"userId": user_email}
            service.licenseAssignments().insert(
                productId=product_id,
                skuId=sku_id,
                body=body
            ).execute()
            logger.info("Successfully assigned license %s/%s to %s", product_id, sku_id, user_email)
            return True, None
        except HttpError as err:
            msg = f"HTTP {err.resp.status}: {err.reason}"
            logger.error("Failed to assign license to %s: %s", user_email, msg)
            return False, msg
        except Exception as ex:
            msg = str(ex)
            logger.error("Unexpected error assigning license to %s: %s", user_email, msg)
            return False, msg
