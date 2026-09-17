"""FastAPI server for the Enterprise HR Agentic Portal Web UI & API."""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from backend.hr_agents.auth_patch import ensure_vertex_auth

ensure_vertex_auth()

from google.adk.runners import InMemoryRunner
from google.genai import types
from backend.hr_agents import root_agent
from backend.hr_agents.agent_gateway import agent_gateway
from backend.hr_agents.security_guardrails import guardrails_engine
from backend.hr_agents.tools import (
    get_authenticated_session_employee_id,
    get_personal_info as get_employee_profile,
    get_employee_balances,
    list_tickets,
    update_ticket_status,
)

app = FastAPI(
    title="Altostrat Singapore Enterprise HR Portal & Agent API",
    version="1.6.0",
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
  target_agent: Optional[str] = "root_agent"


class TicketUpdateRequest(BaseModel):
  ticket_id: str
  new_status: str


class SecurityTestRequest(BaseModel):
  text: str


@app.get("/api/gateway/status")
async def get_agent_gateway_status():
  """Returns Agent Gateway configuration, downstream agent registry, and recent audit trail."""
  return agent_gateway.get_gateway_status()


@app.post("/api/security/test")
async def test_security_guardrails(req: SecurityTestRequest):
  """Tests Model Armor & Cloud DLP masking directly on sample input text."""
  scan_res = guardrails_engine.inspect_and_mask_input(req.text)
  return scan_res


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
      "gateway_status": agent_gateway.get_gateway_status(),
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
async def chat_endpoint(req: ChatRequest, request: Request):
  """Executes Agent Gateway evaluation, Model Armor / Cloud DLP masking, and ADK agent turn."""
  t0 = time.time()
  user_id = req.user_id or get_authenticated_session_employee_id()
  target_agent = req.target_agent or "root_agent"

  # 1. Evaluate Agent Gateway Routing & User Context (Prepared Pass-Through Mode)
  gateway_eval = agent_gateway.evaluate_routing(
      user_id=user_id,
      message=req.message,
      target_agent=target_agent,
      headers=dict(request.headers),
  )

  # 2. Model Armor & Cloud DLP Input Scan
  input_scan = guardrails_engine.inspect_and_mask_input(req.message)

  if input_scan["blocked"]:
    latency_sec = round(time.time() - t0, 2)
    rule_info = input_scan["model_armor_findings"][0]
    blocked_msg = (
        "🛡️ **[Model Armor セキュリティ遮断 (SDD RSK-02)]**\n\n"
        "入力テキストにプロンプトインジェクションまたはシステム命令の上書きパターンが検出されたため、"
        "AIセキュリティ＆ガードレール層（Model Armor）によりリクエストを遮断しました。\n\n"
        f"- **検出ルールID**: `{rule_info['rule_id']}`\n"
        f"- **脅威カテゴリ**: {rule_info['description']}\n"
        f"- **Agent Gateway 監査ID**: `{gateway_eval['audit_id']}` (`{gateway_eval['policy_mode']}`)"
    )
    return {
        "session_id": req.session_id or "blocked-session",
        "user_id": user_id,
        "response": blocked_msg,
        "tools": [],
        "security_guardrails": input_scan,
        "agent_gateway": gateway_eval,
        "latency_sec": latency_sec,
    }

  # Use DLP-sanitized text for LLM execution
  sanitized_user_message = input_scan["sanitized_text"]

  session_id = req.session_id or _SESSIONS.get(user_id)
  if not session_id:
    session = await _RUNNER.session_service.create_session(
        app_name="hr_agents", user_id=user_id
    )
    session_id = session.id
    _SESSIONS[user_id] = session_id

  content = types.Content(
      role="user", parts=[types.Part.from_text(text=sanitized_user_message)]
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

  # 3. Cloud DLP Output Scan (prevent accidental PII leakage in LLM output)
  output_scan = guardrails_engine.inspect_and_mask_output(response_text)
  final_response_text = output_scan["sanitized_text"]

  # Prepend clear DLP masking banner if PII was caught and masked in user input
  if input_scan["dlp_masked"]:
    dlp_summary = ", ".join(
        f"`{f['label']}` ➔ `{f['masked_as']}`" for f in input_scan["dlp_findings"]
    )
    dlp_banner = (
        f"> 🛡️ **[Cloud DLP & Model Armor 自動マスキング保護済]**\n"
        f"> 入力された個人情報（SPII）を検出し、LLM およびログへ送信する前に自動墨消し（De-identification）を実行しました：{dlp_summary}\n"
        f"> **マスキング後の送信テキスト**: `{sanitized_user_message}`\n\n"
    )
    final_response_text = dlp_banner + final_response_text

  latency_sec = round(time.time() - t0, 2)
  return {
      "session_id": session_id,
      "user_id": user_id,
      "response": final_response_text,
      "tools": invoked_tools,
      "security_guardrails": {
          "model_armor_status": input_scan["model_armor_status"],
          "dlp_masked": input_scan["dlp_masked"] or output_scan["dlp_masked"],
          "dlp_findings": input_scan["dlp_findings"] + output_scan["dlp_findings"],
          "sanitized_input": sanitized_user_message,
          "scan_latency_ms": round(input_scan["latency_ms"] + output_scan["latency_ms"], 2),
      },
      "agent_gateway": gateway_eval,
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
          " Profile), and ServiceImmediately ITSM MCP (Support Tickets) with"
          " Model Armor / Cloud DLP & Agent Gateway governance."
      ),
      "url": f"{service_url}/a2a",
      "version": "1.6.0",
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
async def handle_a2a_jsonrpc(payload: Dict[str, Any], request: Request):
  """Handles A2A JSON-RPC 2.0 requests (message/send, tasks/send) from Gemini Enterprise."""
  rpc_id = payload.get("id", "1")
  params = payload.get("params", {})

  user_text = ""
  msg_obj = params.get("message", {})
  for part in msg_obj.get("parts", []):
    if isinstance(part, dict) and ("text" in part or part.get("type") == "text"):
      user_text += part.get("text", "")
  if not user_text:
    user_text = str(params.get("input", params.get("query", "Hello")))

  user_id = get_authenticated_session_employee_id()
  task_id = params.get("id") or f"task-{int(time.time())}"

  # Evaluate Agent Gateway & Security Guardrails
  agent_gateway.evaluate_routing(user_id=user_id, message=user_text, target_agent="root_agent")
  input_scan = guardrails_engine.inspect_and_mask_input(user_text)
  if input_scan["blocked"]:
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": {
            "id": task_id,
            "status": {"state": "completed"},
            "artifacts": [{
                "parts": [{"type": "text", "text": "🛡️ Model Armor Security Block: Prompt injection detected."}]
            }],
        },
    }

  sanitized_text = input_scan["sanitized_text"]

  session_id = _SESSIONS.get(user_id)
  if not session_id:
    session = await _RUNNER.session_service.create_session(
        app_name="hr_agents", user_id=user_id
    )
    session_id = session.id
    _SESSIONS[user_id] = session_id

  content = types.Content(
      role="user", parts=[types.Part.from_text(text=sanitized_text)]
  )
  response_text = ""
  async for event in _RUNNER.run_async(
      user_id=user_id, session_id=session_id, new_message=content
  ):
    if event.content and event.content.parts:
      for part in event.content.parts:
        if event.is_final_response() and part.text:
          response_text += part.text

  output_scan = guardrails_engine.inspect_and_mask_output(response_text)

  return {
      "jsonrpc": "2.0",
      "id": rpc_id,
      "result": {
          "id": task_id,
          "status": {"state": "completed"},
          "artifacts": [
              {
                  "parts": [
                      {
                          "type": "text",
                          "text": output_scan["sanitized_text"],
                      }
                  ]
              }
          ],
      },
  }
