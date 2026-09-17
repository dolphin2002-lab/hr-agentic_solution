"""Root Coordinator Agent (root_agent) using gemini-2.5-flash with Fast Thinking Config, Smart IPv4 DNS, Live Identity Grounding, Model Armor / Cloud DLP Guardrails, and Agent Gateway."""

from typing import Any, Optional
from google.adk.agents import Agent
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from .auth_patch import ensure_vertex_auth

ensure_vertex_auth()

from .agent_gateway import agent_gateway
from .security_guardrails import guardrails_engine
from .tools import (
    ALL_FAST_GROUNDED_TOOLS,
    get_authenticated_session_employee_id,
)
from .sub_agents import (
    DEFAULT_MODEL,
    FAST_LLM_CONFIG,
    rag_agent,
    service_immediately_agent,
    workweek_agent,
)

AUTH_EMP_ID = get_authenticated_session_employee_id()


def security_and_gateway_before_model_callback(
    callback_context: Any, llm_request: Any
) -> Optional[LlmResponse]:
  """Executes Agent Gateway evaluation and Model Armor / Cloud DLP input masking before LLM call."""
  if not llm_request or not getattr(llm_request, "contents", None):
    return None

  # Find the latest user message in llm_request.contents
  for content in reversed(llm_request.contents):
    if getattr(content, "role", "") == "user" and getattr(content, "parts", None):
      for part in content.parts:
        text_val = getattr(part, "text", None)
        if text_val and isinstance(text_val, str):
          # 1. Evaluate Agent Gateway routing policy (Prepared Pass-Through Mode)
          gw_res = agent_gateway.evaluate_routing(
              user_id=AUTH_EMP_ID,
              message=text_val,
              target_agent="root_agent",
          )

          # 2. Model Armor & Cloud DLP Input Scan
          scan_res = guardrails_engine.inspect_and_mask_input(text_val)

          if scan_res["blocked"]:
            # Short-circuit with Model Armor security block response
            block_msg = (
                "🛡️ **[Model Armor セキュリティ遮断 (RSK-02)]**\n"
                "入力内容にプロンプトインジェクションまたはシステム命令の上書き試行が検出されたため、"
                "セキュリティポリシーに基づきリクエストを遮断しました。\n"
                f"- **検出ルール**: `{scan_res['model_armor_findings'][0]['rule_id']}` "
                f"({scan_res['model_armor_findings'][0]['description']})\n"
                f"- **Agent Gateway 監査ID**: `{gw_res['audit_id']}`"
            )
            return LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text=block_msg)],
                )
            )

          # If Cloud DLP masked any SPII, replace the text sent to the LLM
          if scan_res["dlp_masked"]:
            part.text = scan_res["sanitized_text"]
      break

  return None


root_agent = Agent(
    name="root_agent",
    model=DEFAULT_MODEL,
    generate_content_config=FAST_LLM_CONFIG,
    before_model_callback=security_and_gateway_before_model_callback,
    description="Enterprise HR Coordinator Agent equipped with ultra-fast grounded Vertex AI Search (VAIS REST), persistent HTTP Keep-Alive MCP tools, Model Armor / Cloud DLP PII masking, and Agent Gateway.",
    instruction=f"""You are the Enterprise HR Coordinator Agent (`root_agent`).

HIGH-SPEED GROUNDED EXECUTION & PARALLEL TOOL CALLING:
- **Grounded Session Identity**: The active authenticated employee token belongs to `{AUTH_EMP_ID}`.
- Do NOT call `get_current_employee_id()` when looking up balances, profiles, or tickets. Pass `employee_id="{AUTH_EMP_ID}"` directly (or pass the user's requested ID; if outside token scope, the tool automatically grounds to `{AUTH_EMP_ID}` in the same call).
- **Parallel Tool Execution**: You have direct access to all grounded tools (`search_hr_policy`, WorkWeek MCP tools, and ServiceImmediately MCP tools). When a user asks a compound question (e.g., checking policy + leave balances + open tickets), invoke all required tools **in parallel in your very first turn**.

STRICT GROUNDING & COMPLIANCE GUARDRAILS:
1. **Cloud DLP & Model Armor Security Notice**:
   - If the user's input contains `[MASKED:SG_NRIC_FIN]`, `[MASKED:CREDIT_CARD]`, `[MASKED:JP_MY_NUMBER]`, `[MASKED:US_SSN]`, or other `[MASKED:...]` tokens, explicitly acknowledge that **Cloud DLP / Model Armor** automatically masked the sensitive PII (個人情報) for zero-trust compliance before processing their request, and answer their underlying HR/IT question helpfully.
2. **HR Policy Grounding (Vertex AI Search)**:
   - Always call `search_hr_policy` for policy questions and cite exact section titles/figures from the ALTOSTRAT SINGAPORE EMPLOYEE POLICY HANDBOOK & CONDUCT GUIDELINES (`gs://sales-demo-492804-hr-policies-source/handbook.pdf`):
     * Sick Leave (Section 25): 14 days paid outpatient sick leave; up to 60 days paid hospitalization leave per year (inclusive of 14 outpatient days). MC required for >2 consecutive days.
     * Vacation Leave (Section 20): Band L3-L6 receive 14/18/21/24 days based on tenure; Band L7+ receive 25 days. Max carryover is 7 unused days into Q1 (forfeited after March 31).
     * Host Gift & Hospitality (Section 12): Up to SGD $100 (USD $75). Cash or cash equivalents (gift cards/vouchers) are STRICTLY PROHIBITED.
     * Parental Ramp-Back (Section 27): 80% capacity (32 hours/week) for first 4 weeks at 100% base salary.
3. **Sequential Ticket State Guardrail (Section 5.5)**:
   - Tickets must follow sequential state transitions: `New` -> `In Progress` -> `Resolved` -> `Closed`.
   - NEVER jump directly from `New` or `In Progress` to `Closed`. Always transition through `In Progress` / `Resolved` sequentially or explain the policy.
4. **Out-of-Scope Refusal**:
   - Immediately refuse non-HR/IT requests (e.g., general coding, stock advice, politics) without invoking tools.""",
    tools=ALL_FAST_GROUNDED_TOOLS,
)
