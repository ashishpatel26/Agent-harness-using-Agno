"""
Compliance Agent Network
========================
A multi-agent tax compliance system in the "your agent calls our agent" style.

    External agent (ERP, procurement, e-commerce)
            │  A2A / MCP
            ▼
    Compliance Orchestrator (Team)      ← guardrails, memory, session state
            │ delegates
            ├── Tax Calculation Agent   ← tools → deterministic engine, verified by post-hook
            ├── Classification Agent    ← structured output, learning, engine-validated codes
            ├── Reconciliation Agent    ← writes discrepancies into session state
            ├── Exemption Agent         ← knowledge base + certificate checks
            └── Returns Agent           ← knowledge base + human approval before filing

    Month-End Close (Workflow)          ← typed input, deterministic step + agent steps

The rule that makes this safe: agents orchestrate, extract and explain. Every number
comes from tax_engine.py, and hooks reject any answer that skips the engine.
"""

import json
from pathlib import Path
from typing import List, Union

from pydantic import BaseModel, Field

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.exceptions import InputCheckError, OutputCheckError
from agno.guardrails import PIIDetectionGuardrail, PromptInjectionGuardrail
from agno.guardrails.base import BaseGuardrail
from agno.knowledge import Knowledge
from agno.knowledge.embedder.ollama import OllamaEmbedder
from agno.learn import LearnedKnowledgeConfig, LearningMachine, LearningMode
from agno.memory import MemoryManager
from agno.models.ollama import Ollama
from agno.run import RunContext
from agno.run.agent import RunInput, RunOutput
from agno.run.team import TeamRunInput
from agno.team import Team
from agno.tools import tool
from agno.vectordb.chroma import ChromaDb
from agno.vectordb.search import SearchType
from agno.workflow import Step, Workflow
from agno.workflow.types import StepInput, StepOutput

import tax_engine

MODEL_ID = "qwen3.5-4b-16k:latest"
BASE_DIR = Path(__file__).parent
DB_DIR = BASE_DIR / "db"

db = SqliteDb(id="tax-compliance-db", db_file=str(DB_DIR / "compliance.db"))


def _as_json(data) -> str:
    return json.dumps(data, indent=2)


def _vector_db(name: str) -> ChromaDb:
    return ChromaDb(
        name=name,
        collection=name,
        path=str(DB_DIR / name),
        persistent_client=True,
        search_type=SearchType.hybrid,
        hybrid_rrf_k=60,
        embedder=OllamaEmbedder(id="nomic-embed-text:latest", dimensions=768),
    )


# ---------------------------------------------------------------------------
# Knowledge: the compliance playbook (RAG)
# ---------------------------------------------------------------------------
playbook = Knowledge(
    name="Compliance Playbook",
    vector_db=_vector_db("compliance_playbook"),
    max_results=4,
    contents_db=db,
)


def load_playbook() -> None:
    """Index data/tax_rules.md once. Safe to call on every start."""
    playbook.insert(
        name="Compliance Playbook",
        path=str(BASE_DIR / "data" / "tax_rules.md"),
        skip_if_exists=True,
    )


# ---------------------------------------------------------------------------
# Engine tools: the only way agents touch numbers
# ---------------------------------------------------------------------------
def calculate_sales_tax(ship_to_state: str, lines: List[dict], cert_id: str = "") -> str:
    """
    Calculate sales tax with the deterministic tax engine.

    Args:
        ship_to_state: Two-letter destination state, e.g. "CA"
        lines: Order lines, each {"description": str, "tax_code": str, "amount": number}
        cert_id: Optional exemption certificate ID, e.g. "EX-1001"

    Returns:
        JSON with per-line tax, totals, and the taxability reason for each line
    """
    try:
        return _as_json(tax_engine.calculate(lines, ship_to_state, cert_id or None))
    except ValueError as e:
        return _as_json({"error": str(e)})


def list_jurisdictions_and_codes() -> str:
    """List supported states with base rates, and the valid tax code catalog."""
    return _as_json({
        "state_base_rates_percent": {k: str(v) for k, v in tax_engine.STATE_RATES.items()},
        "tax_codes": tax_engine.TAX_CODES,
    })


def validate_exemption_certificate(cert_id: str, state: str) -> str:
    """
    Check whether an exemption certificate is on file, covers the state, and is unexpired.

    Args:
        cert_id: Certificate ID, e.g. "EX-1001"
        state: Two-letter state the sale ships to
    """
    try:
        return _as_json(tax_engine.validate_certificate(cert_id, state))
    except ValueError as e:
        return _as_json({"error": str(e)})


def read_product_catalog() -> str:
    """Return the unstructured product catalog (sku, description) that needs tax codes."""
    return (BASE_DIR / "data" / "product_catalog.csv").read_text()


# State-aware tools (session state shared across the team)
def run_reconciliation(run_context: RunContext, period: str) -> str:
    """
    Reconcile ERP invoices against the engine and the tax log for a period.
    Saves open discrepancies into session state so other agents can use them.

    Args:
        period: Period as YYYY-MM, e.g. "2026-09"
    """
    result = tax_engine.reconcile(period)
    run_context.session_state["filing_period"] = period
    run_context.session_state["open_discrepancies"] = result["discrepancies"]
    return _as_json(result)


def get_filing_summary(period: str, state: str) -> str:
    """
    Get tax liability and filing readiness for one state and period.

    Args:
        period: Period as YYYY-MM
        state: Two-letter state
    """
    try:
        return _as_json(tax_engine.filing_summary(period, state))
    except ValueError as e:
        return _as_json({"error": str(e)})


@tool(requires_confirmation=True)
def submit_return(run_context: RunContext, period: str, state: str) -> str:
    """
    Submit a sales tax return. Irreversible: a human must approve every call.
    This demo simulates submission and does not contact any tax authority.

    Args:
        period: Period as YYYY-MM
        state: Two-letter state
    """
    summary = tax_engine.filing_summary(period, state)
    if not summary["ready_to_file"]:
        return _as_json({"submitted": False,
                         "reason": f"{summary['open_discrepancies']} open discrepancies must be resolved first",
                         "summary": summary})
    filed = run_context.session_state.setdefault("filed_returns", [])
    filed.append({"period": period, "state": state, "tax_liability": summary["tax_liability"]})
    return _as_json({"submitted": True, "confirmation": f"SIM-{state}-{period}", "summary": summary})


# ---------------------------------------------------------------------------
# Hooks: the deterministic engine backstops agent output
# ---------------------------------------------------------------------------
ENGINE_TOOLS = {"calculate_sales_tax", "list_jurisdictions_and_codes", "validate_exemption_certificate"}


def require_engine_call(run_output: RunOutput) -> None:
    """Post-hook: reject any tax answer that was not produced by an engine tool call."""
    called = {t.tool_name for t in (run_output.tools or [])}
    if not called & ENGINE_TOOLS:
        raise OutputCheckError("Blocked: answer did not come from the tax engine. No tool call, no number.")


def require_catalog_codes(run_output: RunOutput) -> None:
    """Post-hook: every tax code the Classification Agent assigns must exist in the engine catalog."""
    content = run_output.content
    if isinstance(content, ClassificationResult):
        bad = [i.tax_code for i in content.items if not tax_engine.is_valid_tax_code(i.tax_code)]
        if bad:
            raise OutputCheckError(f"Blocked: unknown tax codes {bad}. Use only catalog codes.")
        known = read_product_catalog()
        invented = [i.sku for i in content.items if i.sku.startswith("SKU-") and i.sku not in known]
        if invented:
            raise OutputCheckError(f"Blocked: SKUs {invented} are not in the product catalog.")


class EngineOverrideGuardrail(BaseGuardrail):
    """Block requests that try to make the AI set rates or bypass the engine."""

    PATTERNS = ("override the rate", "set the rate", "use a rate of", "skip the engine",
                "ignore the engine", "don't charge tax", "do not charge tax", "make it tax free")

    def check(self, run_input: Union[RunInput, TeamRunInput]) -> None:
        text = run_input.input_content_string().lower()
        if any(p in text for p in self.PATTERNS):
            raise InputCheckError(
                "Rates and taxability are set by the deterministic engine and cannot be overridden by request.",
            )

    async def async_check(self, run_input: Union[RunInput, TeamRunInput]) -> None:
        self.check(run_input)


# ---------------------------------------------------------------------------
# Structured output for classification
# ---------------------------------------------------------------------------
class ClassifiedItem(BaseModel):
    sku: str
    description: str
    tax_code: str = Field(..., description="One code from the catalog, e.g. SW-SAAS")
    confidence: float = Field(..., ge=0, le=1)
    rationale: str = Field(..., description="One sentence: why this code fits")
    needs_review: bool = Field(..., description="True when confidence is below 0.8")


class ClassificationResult(BaseModel):
    items: List[ClassifiedItem]


# ---------------------------------------------------------------------------
# Specialist agents
# ---------------------------------------------------------------------------
tax_calculation_agent = Agent(
    id="tax-calculation-agent",
    name="Tax Calculation Agent",
    role="Real-time sales tax calculation and jurisdiction sourcing",
    model=Ollama(id=MODEL_ID),
    tools=[calculate_sales_tax, list_jurisdictions_and_codes, validate_exemption_certificate],
    instructions="""\
You calculate sales tax. You never compute tax yourself.
1. Always call calculate_sales_tax. Every number you report must come from its result.
2. If a line has no tax code, call list_jurisdictions_and_codes and pick the closest code,
   and say which code you used.
3. Report a table: description, tax code, amount, tax, reason. Then subtotal, tax, total.
4. If the engine returns an error, report the error. Do not guess.\
""",
    post_hooks=[require_engine_call],
    db=db,
    markdown=True,
)

classification_agent = Agent(
    id="classification-agent",
    name="Classification Agent",
    role="Map unstructured product descriptions to catalog tax codes",
    model=Ollama(id=MODEL_ID),
    instructions="""\
You map products to tax codes.
1. Use ONLY the tax codes listed in <valid_tax_codes>. Never invent a code.
2. If no products are given, classify every row in <product_catalog>. Keep the real SKUs.
3. Search learned knowledge for classification rules before deciding.
4. Set needs_review=true when confidence < 0.8.
5. When a user corrects a classification and asks you to remember it, save it as a
   general rule (for example "license keys delivered by email are DIG-DL").\
""",
    # Ground the model in engine data up front: small local models skip tool calls
    # when structured output is on, so the catalog and codes go straight into context.
    additional_context=(
        f"<valid_tax_codes>\n{_as_json(tax_engine.TAX_CODES)}\n</valid_tax_codes>\n"
        f"<product_catalog>\n{read_product_catalog()}</product_catalog>"
    ),
    output_schema=ClassificationResult,
    post_hooks=[require_catalog_codes],
    learning=LearningMachine(
        knowledge=Knowledge(name="Classification Learnings", vector_db=_vector_db("classification_learnings")),
        learned_knowledge=LearnedKnowledgeConfig(mode=LearningMode.AGENTIC),
    ),
    db=db,
)

reconciliation_agent = Agent(
    id="reconciliation-agent",
    name="Reconciliation Agent",
    role="Find discrepancies between the ERP and the tax log, and recommend fixes",
    model=Ollama(id=MODEL_ID),
    tools=[run_reconciliation],
    knowledge=playbook,
    search_knowledge=True,
    instructions="""\
You reconcile ERP invoices with the tax log.
1. Call run_reconciliation for the requested period (YYYY-MM).
2. For each discrepancy, state the invoice, the type, the amounts from the tool, and a fix
   taken from the playbook (search it).
3. Lead with a one-line status: clean, or N discrepancies totalling $X under / over.
4. Do not invent amounts. Copy them from the tool result.\
""",
    db=db,
    markdown=True,
)

exemption_agent = Agent(
    id="exemption-agent",
    name="Exemption Agent",
    role="Validate exemption certificates and explain exemption rules",
    model=Ollama(id=MODEL_ID),
    tools=[validate_exemption_certificate],
    knowledge=playbook,
    search_knowledge=True,
    instructions="""\
You manage exemption certificates.
1. Call validate_exemption_certificate for every certificate and state you are asked about.
2. Search the playbook to explain what the result means for the seller.
3. If a certificate is invalid, say the sale is taxable and what document is needed.\
""",
    db=db,
    markdown=True,
)

returns_agent = Agent(
    id="returns-agent",
    name="Returns Agent",
    role="Check filing readiness and submit returns with human approval",
    model=Ollama(id=MODEL_ID),
    tools=[get_filing_summary, submit_return],
    knowledge=playbook,
    search_knowledge=True,
    instructions="""\
You prepare and file sales tax returns.
1. Call get_filing_summary before anything else.
2. Only call submit_return when the user explicitly asks to file AND ready_to_file is true.
3. If not ready, list what blocks filing and the due date from the playbook.
4. Never claim a return was filed unless submit_return returned submitted=true.\
""",
    db=db,
    markdown=True,
)

specialists = [tax_calculation_agent, classification_agent, reconciliation_agent, exemption_agent, returns_agent]

# ---------------------------------------------------------------------------
# Orchestrator: one entry point for external agents
# ---------------------------------------------------------------------------
compliance_orchestrator = Team(
    id="compliance-orchestrator",
    name="Compliance Orchestrator",
    description="Single entry point for sales tax calculation, product tax classification, "
    "ERP reconciliation, exemption certificates and return filing.",
    model=Ollama(id=MODEL_ID),
    members=specialists,
    instructions="""\
You are the single entry point to a network of tax compliance agents. Callers are
usually other AI agents (ERP, procurement, e-commerce) acting for a business.

Route each request to the right specialist:
- Tax on an order or a rate question       → Tax Calculation Agent
- Products that need tax codes              → Classification Agent
- ERP vs tax log mismatches, month-end      → Reconciliation Agent
- Exemption certificates                    → Exemption Agent
- Filing readiness, submitting returns      → Returns Agent

Chain specialists when needed, e.g. classify unknown products before calculating tax.

Rules:
- Never state a tax number that a specialist did not return from the engine.
- Use what you remember about the customer (states they sell into, filing frequency).
- Current period and open discrepancies are in session state: {filing_period} / {open_discrepancies}.
- Reply concisely, in a form another program can act on: lead with the answer.\
""",
    pre_hooks=[
        PromptInjectionGuardrail(),
        PIIDetectionGuardrail(mask_pii=True),
        EngineOverrideGuardrail(),
    ],
    db=db,
    memory_manager=MemoryManager(
        model=Ollama(id=MODEL_ID),
        db=db,
        additional_instructions="Capture the customer's company, the states they sell into, "
        "their filing frequency, and their ERP system.",
    ),
    # Extract memories after every run. Agentic memory (model decides) was skipped by the small model.
    update_memory_on_run=True,
    session_state={"filing_period": None, "open_discrepancies": [], "filed_returns": []},
    add_session_state_to_context=True,
    add_history_to_context=True,
    num_history_runs=5,
    add_datetime_to_context=True,
    show_members_responses=True,
    markdown=True,
)


# ---------------------------------------------------------------------------
# Month-end close workflow: typed input, deterministic first, AI second
# ---------------------------------------------------------------------------
class CloseRequest(BaseModel):
    period: str = Field(..., pattern=r"^\d{4}-\d{2}$", description="Period as YYYY-MM")
    states: List[str] = Field(default_factory=lambda: list(tax_engine.STATE_RATES))


def engine_close_step(step_input: StepInput) -> StepOutput:
    """Deterministic step: reconcile and summarize every state. No model involved."""
    req = step_input.input
    req = req if isinstance(req, CloseRequest) else CloseRequest.model_validate(req)
    report = {
        "reconciliation": tax_engine.reconcile(req.period),
        "returns": [tax_engine.filing_summary(req.period, s) for s in req.states],
    }
    return StepOutput(content=_as_json(report))


def readiness_memo_step(step_input: StepInput) -> StepOutput:
    """
    Deterministic step: the go/no-go table comes from the engine, not the model.
    The analyst's explanation from the previous step is attached as commentary.
    """
    report = json.loads(step_input.get_step_content("Engine Reconciliation"))
    rows = "\n".join(
        f"| {r['state']} | {r['transactions']} | ${r['tax_liability']} | {r['open_discrepancies']} | "
        f"{'yes' if r['ready_to_file'] else 'no'} |"
        for r in report["returns"]
    )
    ready = [r["state"] for r in report["returns"] if r["ready_to_file"]]
    memo = (
        f"## Month-end close {report['reconciliation']['period']}\n\n"
        f"**Ready to file:** {', '.join(ready) or 'none'}\n\n"
        "| State | Transactions | Tax liability | Open issues | Ready |\n|---|---|---|---|---|\n"
        f"{rows}\n\n### Analyst notes\n\n{step_input.get_last_step_content()}"
    )
    return StepOutput(content=memo)


month_end_close = Workflow(
    id="month-end-close",
    name="Month-End Close",
    description="Reconcile the period with the engine, explain the gaps, then report filing readiness",
    input_schema=CloseRequest,
    db=db,
    steps=[
        Step(name="Engine Reconciliation", executor=engine_close_step),
        Step(name="Explain Discrepancies", agent=Agent(
            name="Discrepancy Analyst",
            model=Ollama(id=MODEL_ID),
            knowledge=playbook,
            search_knowledge=True,
            instructions="""\
You receive a JSON engine report. For each discrepancy give: invoice, type, amount, and the
fix from the playbook. Copy numbers exactly from the JSON. Do not recalculate.\
""",
        )),
        Step(name="Filing Readiness Memo", executor=readiness_memo_step),
    ],
)
