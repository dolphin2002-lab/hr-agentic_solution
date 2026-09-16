"""Tools for Vertex AI Search (VAIS) Policy RAG and WorkWeek / ServiceImmediately MCP integrations."""

import os
from pathlib import Path
from typing import Any, Dict, List
from google.api_core.client_options import ClientOptions
from google.cloud import discoveryengine_v1 as discoveryengine
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StreamableHTTPConnectionParams

try:
  from dotenv import load_dotenv
  load_dotenv()
except ImportError:
  pass

from .auth_patch import ensure_vertex_auth

ensure_vertex_auth()

LOCAL_POLICY_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "data"
    / "policies"
    / "altostrat_singapore_policy_handbook.md"
)


def search_hr_policy(query: str) -> Dict[str, Any]:
  """Searches the ALTOSTRAT SINGAPORE EMPLOYEE POLICY HANDBOOK & CONDUCT GUIDELINES via Vertex AI Search (VAIS).

  Args:
      query: Employee natural language policy question or search phrase (e.g., sick leave days, host gift card limits, vacation days for 8 years tenure, ramp-back leave).

  Returns:
      Dictionary containing grounded_context, citations, and retrieval_engine metadata.
  """
  project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "sales-demo-492804")
  location = os.environ.get("VAIS_LOCATION", "global")
  engine_id = os.environ.get("VERTEX_AI_SEARCH_ENGINE_ID", "hr-policies-lab-engine")

  client_options = (
      ClientOptions(api_endpoint=f"{location}-discoveryengine.googleapis.com")
      if location != "global"
      else None
  )
  client = discoveryengine.SearchServiceClient(client_options=client_options)
  serving_config = (
      f"projects/{project_id}/locations/{location}/collections/default_collection"
      f"/engines/{engine_id}/servingConfigs/default_search"
  )
  content_spec = discoveryengine.SearchRequest.ContentSearchSpec(
      extractive_content_spec=discoveryengine.SearchRequest.ContentSearchSpec.ExtractiveContentSpec(
          max_extractive_answer_count=3,
          max_extractive_segment_count=3,
      )
  )
  request = discoveryengine.SearchRequest(
      serving_config=serving_config,
      query=query,
      page_size=5,
      content_search_spec=content_spec,
  )

  snippets: List[str] = []
  citations: List[str] = []

  try:
    response = client.search(request)
    for result in response.results:
      doc = result.document
      data = getattr(doc, "derived_struct_data", {}) or {}
      link = data.get("link", "")
      if link and link not in citations:
        citations.append(link)

      for seg in data.get("extractive_segments", []):
        content = seg.get("content") if hasattr(seg, "get") else getattr(seg, "content", None)
        if content:
          snippets.append(content)
      for ans in data.get("extractive_answers", []):
        content = ans.get("content") if hasattr(ans, "get") else getattr(ans, "content", None)
        if content:
          snippets.append(content)
      for snip in data.get("snippets", []):
        snippet_text = snip.get("snippet") if hasattr(snip, "get") else getattr(snip, "snippet", None)
        if snippet_text:
          snippets.append(snippet_text)
  except Exception as e:
    snippets.append(f"Vertex AI Search live warning: {e}")

  # Supplement with full structured sections from local mirror of the same handbook PDF if available
  if LOCAL_POLICY_PATH.exists():
    content = LOCAL_POLICY_PATH.read_text(encoding="utf-8")
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    query_terms = [t.lower() for t in query.split() if len(t) > 2]
    scored = []
    for p in paragraphs:
      score = sum(1 for t in query_terms if t in p.lower())
      if score > 0:
        scored.append((score, p))
    scored.sort(key=lambda x: x[0], reverse=True)
    for _, p in scored[:4]:
      if p not in snippets:
        snippets.append(p)

  if not citations:
    citations.append("gs://sales-demo-492804-hr-policies-source/handbook.pdf")

  return {
      "status": "success",
      "retrieval_engine": f"Vertex AI Search ({serving_config})",
      "grounded_context": "\n\n---\n\n".join(snippets),
      "citations": citations,
  }


# Live Stateless MCP Toolsets for WorkWeek (/work-week/mcp/) and ServiceImmediately (/service-immediately/mcp/)
WORKWEEK_MCP_URL = os.environ.get(
    "WORKWEEK_MCP_URL",
    "https://mock-saas.aishprabhat.demo.altostrat.com/work-week/mcp/",
)
WORKWEEK_MCP_TOKEN = os.environ.get("WORKWEEK_MCP_TOKEN", "")

INCIDENT_MCP_URL = os.environ.get(
    "INCIDENT_MCP_URL",
    "https://mock-saas.aishprabhat.demo.altostrat.com/service-immediately/mcp/",
)
INCIDENT_MCP_TOKEN = os.environ.get("INCIDENT_MCP_TOKEN", "")

workweek_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=WORKWEEK_MCP_URL,
        headers={"X-MCP-Token": WORKWEEK_MCP_TOKEN},
    )
)

serviceimmediately_mcp = McpToolset(
    connection_params=StreamableHTTPConnectionParams(
        url=INCIDENT_MCP_URL,
        headers={"X-MCP-Token": INCIDENT_MCP_TOKEN},
    )
)
