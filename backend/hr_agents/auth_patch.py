"""Authentication helper that bridges gcloud CLI credentials to Application Default Credentials (ADC)."""

import os
import subprocess
import time
import google.auth
from google.oauth2.credentials import Credentials


class GcloudCliCredentials(Credentials):
  """Refreshes OAuth2 token automatically via `gcloud auth print-access-token`."""

  def __init__(self, quota_project_id: str = "sales-demo-492804"):
    token = self._fetch_token()
    super().__init__(token=token, quota_project_id=quota_project_id)
    self._last_refresh = time.time()

  def _fetch_token(self) -> str:
    return subprocess.check_output(
        ["gcloud", "auth", "print-access-token"], text=True
    ).strip()

  def refresh(self, request=None):
    self.token = self._fetch_token()
    self._last_refresh = time.time()

  @property
  def expired(self) -> bool:
    return (time.time() - self._last_refresh) > 1500

  @property
  def valid(self) -> bool:
    if self.expired:
      self.refresh(None)
    return bool(self.token)


def ensure_vertex_auth():
  """Patches google.auth.default so ADK Web and Vertex AI Search use active gcloud CLI credentials."""
  os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
  os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "sales-demo-492804")
  os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

  project = os.environ.get("GOOGLE_CLOUD_PROJECT", "sales-demo-492804")

  def _patched_default(scopes=None, request=None, quota_project_id=None, default_scopes=None):
    q_proj = quota_project_id or project
    return GcloudCliCredentials(quota_project_id=q_proj), project

  google.auth.default = _patched_default
