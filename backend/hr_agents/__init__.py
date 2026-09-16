"""HR Agents ADK Package exposing agent module and root_agent for adk web and adk eval."""

from .auth_patch import ensure_vertex_auth

ensure_vertex_auth()

from . import agent
from .agent import root_agent

__all__ = ["agent", "root_agent"]
