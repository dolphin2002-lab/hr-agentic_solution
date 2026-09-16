"""Authentication & Network helper that bridges gcloud CLI credentials to ADC, bypasses Cloudtop mTLS lag, and forces IPv4 on googleapis.com/altostrat.com to eliminate 30s IPv6 timeouts."""

import os
import socket
import subprocess
import time
from typing import Optional

# 1. Disable mTLS client cert probing (aiplatform.mtls.googleapis.com) which adds 15-25s latency per call
os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"

import google.auth
import google.auth.transport.mtls as mtls
from google.oauth2.credentials import Credentials

mtls.has_default_client_cert_source = lambda: False
mtls.should_use_client_cert = lambda: False

# 2. Singleton OAuth2 Token Caching (caches gcloud token in memory for 30 minutes)
_CACHED_TOKEN: Optional[str] = None
_LAST_FETCH_TIME: float = 0.0
_TOKEN_TTL_SECONDS: float = 1800.0


def _get_cached_gcloud_token() -> str:
  """Fetches gcloud access token once and caches it in memory for 30 minutes."""
  global _CACHED_TOKEN, _LAST_FETCH_TIME
  now = time.time()
  if _CACHED_TOKEN and (now - _LAST_FETCH_TIME) < _TOKEN_TTL_SECONDS:
    return _CACHED_TOKEN
  _CACHED_TOKEN = subprocess.check_output(
      ["gcloud", "auth", "print-access-token"], text=True
  ).strip()
  _LAST_FETCH_TIME = now
  return _CACHED_TOKEN


class GcloudCliCredentials(Credentials):
  """Refreshes OAuth2 token automatically using cached token."""

  def __init__(self, quota_project_id: str = "sales-demo-492804"):
    token = _get_cached_gcloud_token()
    super().__init__(token=token, quota_project_id=quota_project_id)
    self._last_refresh = _LAST_FETCH_TIME

  def refresh(self, request=None):
    global _CACHED_TOKEN, _LAST_FETCH_TIME
    _CACHED_TOKEN = None
    self.token = _get_cached_gcloud_token()
    self._last_refresh = _LAST_FETCH_TIME

  @property
  def expired(self) -> bool:
    return (time.time() - self._last_refresh) > _TOKEN_TTL_SECONDS

  @property
  def valid(self) -> bool:
    if self.expired:
      self.refresh(None)
    return bool(self.token)


_CRED_INSTANCES = {}


def ensure_vertex_auth():
  """Patches google.auth.default with a singleton cached credential instance, disables mTLS lag, and enforces IPv4 on aiohttp TCPConnector."""
  os.environ["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = "false"
  os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
  os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "sales-demo-492804")
  os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")

  mtls.has_default_client_cert_source = lambda: False
  mtls.should_use_client_cert = lambda: False
  mtls.default_client_cert_source = lambda: None

  # Force IPv4 on aiohttp TCPConnector so async Vertex AI calls connect in 0.4ms without IPv6 Errno 101 retries
  try:
    import aiohttp

    if not getattr(aiohttp.TCPConnector.__init__, "_ipv4_patched", False):
      _orig_tcp_init = aiohttp.TCPConnector.__init__

      def _ipv4_tcp_init(self, *args, **kwargs):
        kwargs["family"] = socket.AF_INET
        return _orig_tcp_init(self, *args, **kwargs)

      _ipv4_tcp_init._ipv4_patched = True
      aiohttp.TCPConnector.__init__ = _ipv4_tcp_init
  except Exception:
    pass

  project = os.environ.get("GOOGLE_CLOUD_PROJECT", "sales-demo-492804")

  def _patched_default(
      scopes=None, request=None, quota_project_id=None, default_scopes=None
  ):
    q_proj = quota_project_id or project
    if q_proj not in _CRED_INSTANCES:
      _CRED_INSTANCES[q_proj] = GcloudCliCredentials(quota_project_id=q_proj)
    return _CRED_INSTANCES[q_proj], project

  google.auth.default = _patched_default
