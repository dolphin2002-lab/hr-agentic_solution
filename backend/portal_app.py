"""FastAPI server for the Enterprise HR Agentic Portal Web UI & API."""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.hr_agents.auth_patch import ensure_vertex_auth

ensure_vertex_auth()

from google.adk.runners import InMemoryRunner
from google.genai import types
from backend.hr_agents import root_agent
from backend.hr_agents.tools import (
    get_authenticated_session_employee_id,
    get_personal_info as get_employee_profile,
    get_employee_balances,
    list_tickets,
    update_ticket_status,
)

app = FastAPI(
    title="Altostrat Singapore Enterprise HR Portal & Agent API",
    version="1.5.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared runner instance for fast session management
_RUNNER = InMemoryRunner(agent=root_agent, app_name="hr_agents")
_SESSIONS: Dict[str, str] = {}


class ChatRequest(BaseModel):
  message: str
  user_id: Optional[str] = None
  session_id: Optional[str] = None


class TicketUpdateRequest(BaseModel):
  ticket_id: str
  new_status: str


@app.get("/api/dashboard")
async def get_dashboard_data():
  """Fetches live grounded employee profile, leave balances, and open tickets from MCP servers."""
  t0 = time.time()
  emp_id = get_authenticated_session_employee_id()

  # Execute live MCP tool calls concurrently in threads
  profile_task = asyncio.to_thread(get_employee_profile, emp_id)
  balances_task = asyncio.to_thread(get_employee_balances, emp_id)
  tickets_task = asyncio.to_thread(list_tickets, emp_id)

  profile, balances, tickets = await asyncio.gather(
      profile_task, balances_task, tickets_task, return_exceptions=True
  )

  return {
      "employee_id": emp_id,
      "profile": profile if not isinstance(profile, Exception) else {"error": str(profile)},
      "balances": balances if not isinstance(balances, Exception) else {"error": str(balances)},
      "tickets": tickets if not isinstance(tickets, Exception) else {"error": str(tickets)},
      "latency_ms": round((time.time() - t0) * 1000, 1),
  }


@app.post("/api/ticket/status")
async def update_ticket_endpoint(req: TicketUpdateRequest):
  """Updates ticket status sequentially via ServiceImmediately MCP."""
  res = await asyncio.to_thread(
      update_ticket_status, req.ticket_id, req.new_status
  )
  return {"result": res}


@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
  """Executes an ADK agent turn and returns response text, invoked tool names, and latency."""
  t0 = time.time()
  user_id = req.user_id or get_authenticated_session_employee_id()

  session_id = req.session_id or _SESSIONS.get(user_id)
  if not session_id:
    session = await _RUNNER.session_service.create_session(
        app_name="hr_agents", user_id=user_id
    )
    session_id = session.id
    _SESSIONS[user_id] = session_id

  content = types.Content(
      role="user", parts=[types.Part.from_text(text=req.message)]
  )

  invoked_tools: List[Dict[str, Any]] = []
  response_text = ""

  async for event in _RUNNER.run_async(
      user_id=user_id, session_id=session_id, new_message=content
  ):
    if event.content and event.content.parts:
      for part in event.content.parts:
        if part.function_call:
          invoked_tools.append({
              "name": part.function_call.name,
              "args": dict(part.function_call.args) if part.function_call.args else {},
          })
        if event.is_final_response() and part.text:
          response_text += part.text

  latency_sec = round(time.time() - t0, 2)
  return {
      "session_id": session_id,
      "user_id": user_id,
      "response": response_text,
      "tools": invoked_tools,
      "latency_sec": latency_sec,
  }


@app.get("/", response_class=HTMLResponse)
async def serve_portal_ui():
  """Serves the modern Enterprise HR Portal Web UI."""
  html_path = os.path.join(os.path.dirname(__file__), "portal_ui.html")
  with open(html_path, "r", encoding="utf-8") as f:
    return HTMLResponse(content=f.read())


@app.get("/.well-known/agent-card.json")
@app.get("/a2a/hr_agents/.well-known/agent-card.json")
async def get_a2a_agent_card():
  """Serves the A2A Agent Card JSON for Gemini Enterprise registration."""
  service_url = os.environ.get(
      "PUBLIC_SERVICE_URL",
      "https://hr-agentic-portal-176121361862.us-central1.run.app",
  )
  return {
      "protocolVersion": "0.2.5",
      "name": "Altostrat Singapore HR Coordinator Agent",
      "description": (
          "Enterprise HR Agentic Solution grounded in Singapore HR Policy"
          " Handbook (Vertex AI Search), WorkWeek HCM MCP (Leave Balances &"
          " Profile), and ServiceImmediately ITSM MCP (Support Tickets)."
      ),
      "url": f"{service_url}/a2a",
      "version": "1.5.0",
      "capabilities": {
          "streaming": False,
          "pushNotifications": False,
          "stateTransitionHistory": True,
      },
      "defaultInputModes": ["text/plain"],
      "defaultOutputModes": ["text/plain"],
      "skills": [
          {
              "id": "hr_policy_search",
              "name": "Singapore HR Policy Grounding",
              "description": (
                  "Answers Singapore HR policy questions grounded in official"
                  " handbook via Vertex AI Search."
              ),
              "tags": ["hr", "policy", "singapore", "vais"],
              "examples": [
                  "How many days of paid outpatient sick leave do full-time employees get in Singapore?"
              ],
          },
          {
              "id": "workweek_leave_balances",
              "name": "WorkWeek HCM Leave Balances & Profile",
              "description": (
                  "Retrieves live employee profile, address, and vacation/sick"
                  " leave balances via WorkWeek MCP."
              ),
              "tags": ["hris", "leave", "workweek", "mcp"],
              "examples": ["Check my current vacation and sick leave balances."],
          },
          {
              "id": "service_immediately_tickets",
              "name": "ServiceImmediately ITSM Tickets",
              "description": (
                  "Lists open IT/HR support tickets and executes sequential"
                  " status transitions via ServiceImmediately MCP."
              ),
              "tags": ["itsm", "tickets", "support", "mcp"],
              "examples": ["List my open IT and HR support tickets."],
          },
      ],
  }


@app.post("/a2a")
@app.post("/a2a/hr_agents")
async def handle_a2a_jsonrpc(payload: Dict[str, Any]):
  """Handles A2A JSON-RPC 2.0 requests (message/send, tasks/send) from Gemini Enterprise."""
  rpc_id = payload.get("id", "1")
  method = payload.get("method", "")
  params = payload.get("params", {})

  # Extract user message text from A2A message/send or tasks/send payload
  user_text = ""
  msg_obj = params.get("message", {})
  for part in msg_obj.get("parts", []):
    if isinstance(part, dict) and ("text" in part or part.get("type") == "text"):
      user_text += part.get("text", "")
  if not user_text:
    user_text = str(params.get("input", params.get("query", "Hello")))

  user_id = get_authenticated_session_employee_id()
  task_id = params.get("id") or f"task-{int(time.time())}"

  session_id = _SESSIONS.get(user_id)
  if not session_id:
    session = await _RUNNER.session_service.create_session(
        app_name="hr_agents", user_id=user_id
    )
    session_id = session.id
    _SESSIONS[user_id] = session_id

  content = types.Content(
      role="user", parts=[types.Part.from_text(text=user_text)]
  )
  response_text = ""
  async for event in _RUNNER.run_async(
      user_id=user_id, session_id=session_id, new_message=content
  ):
    if event.content and event.content.parts:
      for part in event.content.parts:
        if event.is_final_response() and part.text:
          response_text += part.text

  return {
      "jsonrpc": "2.0",
      "id": rpc_id,
      "result": {
          "id": task_id,
          "status": {"state": "completed"},
          "artifacts": [
              {
                  "name": "response",
                  "parts": [{"type": "text", "text": response_text}],
              }
          ],
          "history": [
              msg_obj,
              {
                  "role": "agent",
                  "parts": [{"type": "text", "text": response_text}],
              },
          ],
      },
  }

