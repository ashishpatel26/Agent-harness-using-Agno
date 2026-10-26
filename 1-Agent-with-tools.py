from agno.agent import Agent
from agno.models.ollama import Ollama
from agno.tools.yfinance import YFinanceTools

agent_with_tools = Agent(
    name="Agent with tools",
    model=Ollama(id="qwen3.5-4b-16k:latest"),
    instructions=[
        "Use Yahoo Finance for facts that can change.",
        "Lead with the answer, then show the evidence.",
        "Use a table when comparing companies.",
        "Say when data is unavailable; never invent a value.",
        "Keep the response concise and do not give personalized financial advice.",
    ],
    tools=[
        YFinanceTools(
            enable_company_info=True,
            enable_stock_fundamentals=True,
            enable_company_news=True,
        )
    ],
    add_datetime_to_context=True,
    markdown=True,
)

if __name__ == "__main__":
    agent_with_tools.print_response("What is Apple's current valuation?", stream=True)
