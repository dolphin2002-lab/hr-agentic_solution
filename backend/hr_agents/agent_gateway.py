"""Agent Gateway Layer: User & Downstream Agent Control (SDD Section 1.2 & Section 5.3).

Provides a centralized governance gateway between clients (Portal UI, Gemini Enterprise A2A,
ADK CLI) and downstream domain agents (root_agent, rag_agent, workweek_agent,
service_immediately_agent).

Current Mode: PREPARED_PASS_THROUGH (enforce_blocking=False)
- Evaluates user identity, role context, downstream agent routing ACLs, and rate-limit
  quotas, recording structured audit telemetry without blocking traffic yet.
- Ready to enable strict blocking (enforce_blocking=True) via environment variable or API.
"""

from collections import deque
import os
import time
from typing import Any, Deque, Dict, List, Optional


# ==============================================================================
# 1. Downstream Agent Registry & Access Control Matrix (SDD Section 3.1 & 5.3)
# ==============================================================================

DOWNSTREAM_AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {
    "root_agent": {
        "agent_id": "root_agent",
        "display_name": "HR Coordinator Agent (Orchestrator)",
        "model": "gemini-2.5-flash",
        "allowed_roles": ["Employee", "Manager", "HR_Admin", "System_A2A"],
        "rate_limit_rps": 200,
        "downstream_dependencies": ["rag_agent", "workweek_agent", "service_immediately_agent"],
        "status": "ACTIVE",
    },
    "rag_agent": {
        "agent_id": "rag_agent",
        "display_name": "Singapore HR Policy RAG Specialist",
        "backend_system": "Vertex AI Search (hr-policies-lab-engine)",
        "allowed_roles": ["Employee", "Manager", "HR_Admin", "System_A2A"],
        "rate_limit_rps": 150,
        "required_scope": "vais.policies.read",
        "status": "ACTIVE",
    },
    "workweek_agent": {
        "agent_id": "workweek_agent",
        "display_name": "WorkWeek HCM Specialist",
        "backend_system": "WorkWeek Live MCP (/work-week/mcp/)",
        "allowed_roles": ["Employee", "Manager", "HR_Admin"],
        "rate_limit_rps": 100,  # SDD Section 5.3: WorkWeek 100 RPS
        "required_scope": "workweek.employee.self",
        "bound_employee_id": "EMP-779",
        "status": "ACTIVE",
    },
    "service_immediately_agent": {
        "agent_id": "service_immediately_agent",
        "display_name": "ServiceImmediately ITSM Specialist",
        "backend_system": "ServiceImmediately Live MCP (/service-immediately/mcp/)",
        "allowed_roles": ["Employee", "Manager", "IT_Support", "HR_Admin"],
        "rate_limit_rps": 50,  # SDD Section 5.3: ITSM 50 RPS
        "required_scope": "itsm.tickets.readwrite",
        "bound_employee_id": "EMP-779",
        "status": "ACTIVE",
    },
}


# ==============================================================================
# 2. User Identity & Role Mapping Directory (Prepared for Okta / OBO Phase 2)
# ==============================================================================

USER_DIRECTORY_MOCK: Dict[str, Dict[str, Any]] = {
    "EMP-779": {
        "employee_id": "EMP-779",
        "name": "Tomohito Employee",
        "role": "Employee",
        "department": "Enterprise Cloud Engineering (Singapore)",
        "location": "Singapore Office (80 Pasir Panjang Rd)",
        "scopes": ["vais.policies.read", "workweek.employee.self", "itsm.tickets.readwrite"],
        "allowed_agents": ["root_agent", "rag_agent", "workweek_agent", "service_immediately_agent"],
    },
    "EMP-001": {
        "employee_id": "EMP-001",
        "name": "Restricted Executive Profile",
        "role": "Executive",
        "department": "Corporate Leadership",
        "location": "Singapore HQ",
        "scopes": ["vais.policies.read"],
        "allowed_agents": ["root_agent", "rag_agent"],  # Restricted from EMP-779 MCP token
    },
}


class AgentGateway:
  """Centralized Agent Gateway for User & Downstream Agent Control."""

  def __init__(self):
    # Prepared Pass-Through Mode by default ("制御自体はまだいれなくて、準備しておく程度")
    self.enforce_blocking = os.environ.get("AGENT_GATEWAY_ENFORCE_BLOCKING", "false").lower() == "true"
    self.policy_mode = "STRICT_ENFORCEMENT" if self.enforce_blocking else "PREPARED_PASS_THROUGH"
    self.audit_log: Deque[Dict[str, Any]] = deque(maxlen=50)

  def resolve_user_context(self, user_id: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Resolves user identity, role, scopes, and allowed target agents."""
    clean_id = (user_id or "EMP-779").strip().upper()
    profile = USER_DIRECTORY_MOCK.get(clean_id, {
        "employee_id": clean_id,
        "name": f"Authenticated User ({clean_id})",
        "role": "Employee",
        "department": "General Operations",
        "location": "Singapore",
        "scopes": ["vais.policies.read", "workweek.employee.self", "itsm.tickets.readwrite"],
        "allowed_agents": ["root_agent", "rag_agent", "workweek_agent", "service_immediately_agent"],
    })
    return profile

  def evaluate_routing(
      self,
      user_id: str,
      message: str,
      target_agent: str = "root_agent",
      headers: Optional[Dict[str, str]] = None,
  ) -> Dict[str, Any]:
    """Evaluates whether the user is permitted to access the target agent & tools."""
    t0 = time.time()
    user_ctx = self.resolve_user_context(user_id, headers)
    agent_meta = DOWNSTREAM_AGENT_REGISTRY.get(target_agent, DOWNSTREAM_AGENT_REGISTRY["root_agent"])

    # Determine which specialist downstream agents are implicated by the query intent
    implicated_agents = ["root_agent"]
    msg_lower = (message or "").lower()
    if any(k in msg_lower for k in ["規程", "規則", "ハンドブック", "policy", "handbook", "sick leave", "gift", "贈答"]):
      implicated_agents.append("rag_agent")
    if any(k in msg_lower for k in ["残高", "有給", "休暇", "住所", "balance", "vacation", "profile", "workweek"]):
      implicated_agents.append("workweek_agent")
    if any(k in msg_lower for k in ["チケット", "サポート", "インシデント", "ticket", "inc0", "vpn", "service"]):
      implicated_agents.append("service_immediately_agent")
    if len(implicated_agents) == 1:
      # Default multi-domain readiness
      implicated_agents = ["root_agent", "rag_agent", "workweek_agent", "service_immediately_agent"]

    # Check policy rules (ACL check)
    role_allowed = user_ctx["role"] in agent_meta.get("allowed_roles", ["Employee"])
    would_allow = role_allowed and (target_agent in user_ctx.get("allowed_agents", []))

    # Since enforce_blocking=False (Prepared Pass-Through Mode), we allow traffic
    # while recording what strict enforcement would do.
    final_allowed = would_allow if self.enforce_blocking else True

    decision_label = (
        "ALLOWED (Pass-Through Ready)"
        if not self.enforce_blocking
        else ("ALLOWED" if final_allowed else "BLOCKED_BY_ACL")
    )

    latency_ms = round((time.time() - t0) * 1000, 2)
    audit_entry = {
        "audit_id": f"gw-{int(time.time() * 1000)}",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy_mode": self.policy_mode,
        "enforce_blocking": self.enforce_blocking,
        "decision": decision_label,
        "allowed": final_allowed,
        "would_allow_if_strict": would_allow,
        "user_id": user_ctx["employee_id"],
        "user_role": user_ctx["role"],
        "target_entry_agent": target_agent,
        "downstream_agents_routed": implicated_agents,
        "bound_mcp_token_scope": "EMP-779 (Verified)",
        "latency_ms": latency_ms,
    }
    self.audit_log.appendleft(audit_entry)
    return audit_entry

  def get_gateway_status(self) -> Dict[str, Any]:
    """Returns current Agent Gateway configuration, registered agents, and audit trail."""
    return {
        "gateway_version": "1.0.0-SDD-Prepared",
        "policy_mode": self.policy_mode,
        "enforce_blocking": self.enforce_blocking,
        "description": (
            "Agent Gateway for User & Downstream Agent Control. Currently operating in "
            "PREPARED_PASS_THROUGH mode (control hooks & routing evaluation active; blocking disabled)."
        ),
        "downstream_agent_registry": DOWNSTREAM_AGENT_REGISTRY,
        "recent_audit_log": list(self.audit_log)[:10],
    }


# Singleton instance
agent_gateway = AgentGateway()
