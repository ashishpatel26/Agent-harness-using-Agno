"""
Agentic Sandbox with E2B - One Isolated Sandbox per Agent
=========================================================
This example gives every agent its own E2B sandbox (a cloud microVM).
Code an agent writes runs in *its* sandbox, never on your machine, and
never in another agent's sandbox.

Two agents work as a small pipeline:
- Data Analyst: generates a dataset and computes statistics in its sandbox
- Report Checker: re-computes the numbers independently in its own sandbox

Key concepts:
- One E2BTools instance = one sandbox. Create one per agent for isolation.
- include_tools trims the toolkit to the few tools a small model handles well
- Agents share results as messages (text), not through a shared filesystem
- A deterministic check proves isolation: a file written in one sandbox
  does not exist in the other
- Sandboxes cost money while alive, so they are always killed in `finally`

Setup:
- Get a free API key at https://e2b.dev and set E2B_API_KEY
- uv add e2b-code-interpreter
- ollama pull qwen3.5-4b-16k (or change MODEL)
"""

import os

from agno.agent import Agent
from agno.models.ollama import Ollama
from agno.tools.e2b import E2BTools

MODEL = "qwen3.5-4b-16k:latest"
SANDBOX_TOOLS = ["run_python_code", "list_files", "read_file_content", "write_file_content"]


def sandboxed_agent(name: str, role: str) -> tuple[Agent, E2BTools]:
    """Build an agent wired to a brand-new sandbox of its own."""
    box = E2BTools(timeout=300, include_tools=SANDBOX_TOOLS)
    agent = Agent(
        name=name,
        model=Ollama(id=MODEL, request_params={"think": False}),
        tools=[box],
        instructions=[
            role,
            "Always compute with run_python_code; never do arithmetic in your head.",
            "Use print() so results show up in the tool output.",
            "Report the exact numbers the code printed.",
        ],
        markdown=True,
    )
    return agent, box


def show_sandbox_use(agent: Agent, box: E2BTools, run) -> None:
    """Print every tool call the agent made and which sandbox ran it."""
    calls = run.tools or []
    if not calls:
        print(f"[{agent.name}] WARNING: no sandbox used - answer was not computed")
    for call in calls:
        print(f"[{agent.name}] sandbox {box.sandbox.sandbox_id} ran {call.tool_name}")
        print(f"    code:   {(call.tool_args or {}).get('code', call.tool_args)}")
        print(f"    output: {str(call.result)[:200]}")


def check_isolation(a: E2BTools, b: E2BTools) -> None:
    """No LLM involved: prove the two sandboxes don't share a filesystem."""
    path = "/home/user/analyst_only.txt"
    a.sandbox.files.write(path, "visible to the analyst only")
    assert a.sandbox.sandbox_id != b.sandbox.sandbox_id, "agents share one sandbox"
    assert a.sandbox.files.exists(path), "file missing in its own sandbox"
    assert not b.sandbox.files.exists(path), "file leaked into the other sandbox"
    print(f"Isolation OK: {a.sandbox.sandbox_id} != {b.sandbox.sandbox_id}, file not shared")


if __name__ == "__main__":
    if not os.getenv("E2B_API_KEY"):
        raise SystemExit("Set E2B_API_KEY first (free key at https://e2b.dev).")

    boxes: list[E2BTools] = []
    try:
        analyst, analyst_box = sandboxed_agent(
            "Data Analyst", "You analyse data with Python inside your own sandbox."
        )
        boxes.append(analyst_box)
        checker, checker_box = sandboxed_agent(
            "Report Checker", "You verify another agent's numbers by recomputing them yourself."
        )
        boxes.append(checker_box)

        check_isolation(analyst_box, checker_box)

        task = (
            "Monthly sales are [120, 135, 150, 110, 160, 175]. "
            "Compute the total, the mean and the best month (1-based index)."
        )
        analysis = analyst.run(task)
        print(f"\n--- {analyst.name} ---")
        show_sandbox_use(analyst, analyst_box, analysis)
        print(analysis.content)

        verdict = checker.run(
            f"Task: {task}\nAnother agent reported:\n{analysis.content}\n"
            "Recompute everything in your sandbox and say CONFIRMED or MISMATCH, with the numbers."
        )
        print(f"\n--- {checker.name} ---")
        show_sandbox_use(checker, checker_box, verdict)
        print(verdict.content)
    finally:
        for box in boxes:  # sandboxes bill while alive
            box.sandbox.kill()
        print(f"\nKilled {len(boxes)} sandbox(es).")
