from typing import List, Literal, Optional

from agno.agent import Agent
from agno.models.ollama import Ollama
from agno.tools.yfinance import YFinanceTools
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Input Schema — what the agent accepts
# ---------------------------------------------------------------------------
class AnalysisRequest(BaseModel):
    """Structured input for requesting a stock analysis."""

    ticker: str = Field(
        ...,
        min_length=1,
        max_length=10,
        pattern=r"^[A-Za-z][A-Za-z0-9.-]*$",
        description="Stock ticker symbol (e.g., NVDA, AAPL)",
    )
    analysis_type: Literal["quick", "deep"] = Field(
        default="quick",
        description="quick = summary only, deep = full analysis with drivers/risks",
    )
    include_risks: bool = Field(
        default=True, description="Whether to include risk analysis"
    )


# ---------------------------------------------------------------------------
# Output Schema — what the agent returns
# ---------------------------------------------------------------------------
class StockAnalysis(BaseModel):
    """Structured output for stock analysis."""

    ticker: str = Field(
        ...,
        min_length=1,
        max_length=10,
        pattern=r"^[A-Za-z][A-Za-z0-9.-]*$",
        description="Stock ticker symbol",
    )
    company_name: str = Field(..., description="Full company name")
    current_price: Optional[float] = Field(
        None, ge=0, description="Current stock price in USD, if available"
    )
    summary: str = Field(..., description="One-line summary of the stock")
    key_drivers: Optional[List[str]] = Field(
        None, description="Key growth drivers (if deep analysis)"
    )
    key_risks: Optional[List[str]] = Field(
        None, description="Key risks (if include_risks=True)"
    )
    recommendation: Literal["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"] = Field(
        ..., description="Research outlook based on the available data"
    )

    # ---------------------------------------------------------------------------


# Agent Instructions
# ---------------------------------------------------------------------------
instructions = """\
You are a Finance Agent that produces structured stock analyses.

## Input Parameters

You receive structured requests with:
- ticker: The stock to analyze
- analysis_type: "quick" (summary only) or "deep" (full analysis)
- include_risks: Whether to include risk analysis

## Workflow

1. Fetch data for the requested ticker
2. If analysis_type is "deep", identify key drivers
3. If include_risks is True, identify key risks
4. Provide a clear recommendation

## Rules

- Source: Yahoo Finance
- Match output to input parameters — don't include drivers for "quick" analysis
- Missing market data? Use null. Never estimate or invent a value.
- Recommendation must be one of: Strong Buy, Buy, Hold, Sell, Strong Sell\
"""

agent_with_typed_input_output = Agent(
    name="Agent with typed input/output",
    model=Ollama(id="qwen3.5-4b-16k:latest"),
    instructions=instructions,
    tools=[
        YFinanceTools(
            enable_company_info=True,
            enable_stock_fundamentals=True,
            enable_company_news=True,
        )
    ],
    input_schema=AnalysisRequest,
    output_schema=StockAnalysis,
    add_datetime_to_context=True,  # Optional: include current date/time in context
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run the Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Option 1: Pass input as a dict
    response_1 = agent_with_typed_input_output.run(
        input={
            "ticker": "NVDA",
            "analysis_type": "deep",
            "include_risks": True,
        }
    )

    # Access the typed output
    analysis_1: StockAnalysis = response_1.content

    print(f"\n{'=' * 60}")
    print(f"Stock Analysis: {analysis_1.company_name} ({analysis_1.ticker})")
    print(f"{'=' * 60}")
    price_1 = (
        f"${analysis_1.current_price:.2f}"
        if analysis_1.current_price is not None
        else "N/A"
    )
    print(f"Price: {price_1}")
    print(f"Summary: {analysis_1.summary}")
    if analysis_1.key_drivers:
        print("\nKey Drivers:")
        for driver in analysis_1.key_drivers:
            print(f"  • {driver}")
    if analysis_1.key_risks:
        print("\nKey Risks:")
        for risk in analysis_1.key_risks:
            print(f"  • {risk}")
    print(f"\nRecommendation: {analysis_1.recommendation}")
    print(f"{'=' * 60}\n")

    # Option 2: Pass input as a Pydantic model
    request = AnalysisRequest(
        ticker="AAPL",
        analysis_type="quick",
        include_risks=False,
    )
    response_2 = agent_with_typed_input_output.run(input=request)

    # Access the typed output
    analysis_2: StockAnalysis = response_2.content

    print(f"\n{'=' * 60}")
    print(f"Stock Analysis: {analysis_2.company_name} ({analysis_2.ticker})")
    print(f"{'=' * 60}")
    price_2 = (
        f"${analysis_2.current_price:.2f}"
        if analysis_2.current_price is not None
        else "N/A"
    )
    print(f"Price: {price_2}")
    print(f"Summary: {analysis_2.summary}")
    if analysis_2.key_drivers:
        print("\nKey Drivers:")
        for driver in analysis_2.key_drivers:
            print(f"  • {driver}")
    if analysis_2.key_risks:
        print("\nKey Risks:")
        for risk in analysis_2.key_risks:
            print(f"  • {risk}")
    print(f"\nRecommendation: {analysis_2.recommendation}")
    print(f"{'=' * 60}\n")
