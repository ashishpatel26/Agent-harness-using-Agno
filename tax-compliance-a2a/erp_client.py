"""
External ERP Agent: "have your agent call our agent"
====================================================
Plays the customer's side: a business AI agent inside an ERP that knows nothing
about tax. Instead of integrating tax APIs, it delegates compliance questions to
the Compliance Orchestrator over A2A, or uses its tools over MCP.

Start the server first:
    uv run tax-compliance-a2a/server.py

Then, in another terminal:
    uv run tax-compliance-a2a/erp_client.py          # A2A (default)
    uv run tax-compliance-a2a/erp_client.py --mcp    # MCP
"""

import asyncio
import sys

from agno.agent import Agent
from agno.client.a2a import A2AClient
from agno.models.ollama import Ollama
from agno.tools.mcp import MCPTools

SERVER = "http://localhost:7777"
ORCHESTRATOR_A2A = f"{SERVER}/a2a/teams/compliance-orchestrator"
CUSTOMER = "ap-agent@northwind.example"

compliance = A2AClient(ORCHESTRATOR_A2A, timeout=600)


async def ask_compliance_network(request: str) -> str:
    """
    Delegate any sales tax, classification, reconciliation, exemption or filing task
    to the external compliance agent network over A2A.

    Args:
        request: The full task in plain language, including states, amounts and IDs
    """
    result = await compliance.send_message(request, context_id="northwind-erp", user_id=CUSTOMER)
    return result.content


ERP_INSTRUCTIONS = """\
You are the accounts-payable agent inside Northwind Retail's ERP.
You do not know tax rules. For anything about tax, delegate to the compliance network
and pass its answer through faithfully. Never change its numbers.\
"""

TASKS = [
    "We sell from CA into CA, NY and TX and file monthly. Our ERP is NetSuite. "
    "Calculate tax for order SO-5512 shipping to NY: 2 ergonomic chairs at $450 total (TPP-GEN) "
    "and a 1-year CloudDesk SaaS subscription for $1,200 (SW-SAAS).",
    "Customer Fabrikam gave certificate EX-1002 for a TX order. Is it still valid?",
    "Run the September 2026 (2026-09) reconciliation and tell me what blocks the TX return.",
]


async def run_a2a() -> None:
    card = compliance.get_agent_card()
    print(f"Discovered remote agent: {card.name if card else 'no agent card'}\n")

    erp_agent = Agent(
        name="Northwind ERP Agent",
        model=Ollama(id="qwen3.5-4b-16k:latest"),
        tools=[ask_compliance_network],
        instructions=ERP_INSTRUCTIONS,
        markdown=True,
    )
    for task in TASKS:
        await erp_agent.aprint_response(task, stream=True)


async def run_mcp() -> None:
    # The MCP server publishes run_team / run_agent / run_workflow as tools.
    # Only run_team: one door into the network. Local models need a long read timeout.
    async with MCPTools(url=f"{SERVER}/mcp", transport="streamable-http",
                        include_tools=["run_team"], timeout_seconds=600) as mcp_tools:
        erp_agent = Agent(
            name="Northwind ERP Agent (MCP)",
            model=Ollama(id="qwen3.5-4b-16k:latest"),
            tools=[mcp_tools],
            instructions=ERP_INSTRUCTIONS + "\nUse run_team with team id 'compliance-orchestrator'.",
            markdown=True,
        )
        await erp_agent.aprint_response(TASKS[0], stream=True)


if __name__ == "__main__":
    asyncio.run(run_mcp() if "--mcp" in sys.argv else run_a2a())
