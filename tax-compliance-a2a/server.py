"""
Compliance Network Server (AgentOS)
===================================
Exposes the whole agent network to the outside world over three protocols:

- REST    http://localhost:7777/docs           AgentOS API (also used by os.agno.com / Agent UI)
- A2A     http://localhost:7777/a2a/...        Agent-to-Agent: other agents call ours
- MCP     http://localhost:7777/mcp            Model Context Protocol: any MCP client gets our tools

A2A agent cards (capability discovery):
    /a2a/teams/compliance-orchestrator/.well-known/agent-card.json
    /a2a/agents/tax-calculation-agent/.well-known/agent-card.json

Run:
    uv run tax-compliance-a2a/server.py
"""

from agno.os import AgentOS
from agno.os.config import MCPConfig

from agents import compliance_orchestrator, load_playbook, month_end_close, specialists

load_playbook()

agent_os = AgentOS(
    id="compliance-network",
    description="Tax compliance agent network: calculation, classification, reconciliation, exemptions, returns",
    agents=specialists,
    teams=[compliance_orchestrator],
    workflows=[month_end_close],
    a2a_interface=True,  # Agent-to-Agent protocol endpoints under /a2a
    # MCP server at /mcp (run_agent, run_team, run_workflow, ...). Longer tool timeout for local models.
    mcp=MCPConfig(default_tools=True, tool_run_timeout_seconds=600),
)
app = agent_os.get_app()

if __name__ == "__main__":
    # Pass the app object: uvicorn can't import a module path from a hyphenated folder.
    agent_os.serve(app)
