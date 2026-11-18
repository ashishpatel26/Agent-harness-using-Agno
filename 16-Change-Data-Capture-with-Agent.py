"""
Change Data Capture (CDC) with Agent - Inventory Change Watcher
===============================================================
This example shows how an agent can react to *changes* in a database
instead of answering questions about its current state.

CDC records every INSERT / UPDATE / DELETE as an event with the row's
before and after values. Here SQLite triggers write those events into a
`cdc_events` table (the same idea as Debezium reading a Postgres WAL).
The agent consumes new events, explains what changed and flags risky ones.

Key concepts:
- Trigger-based CDC: the database captures changes, the app code stays unchanged
- Change events carry op, before and after (JSON), so diffs are exact
- A checkpoint (last processed sequence number) lives in session_state,
  so each run reads only events it hasn't seen (the checkpoint moves on read,
  so this is at-most-once; commit it after processing for at-least-once)
- Detection rules are plain code; the agent explains and prioritises

Example prompts to try:
- "Process new changes and flag anything risky"
- "What happened to product 3 over time?"
- "Replay the last 5 events" (after resetting the checkpoint)
"""

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from agno.agent import Agent
from agno.db.sqlite import SqliteDb
from agno.models.ollama import Ollama
from agno.run import RunContext

SHOP_DB = Path("data/cdc_shop.db")
PRICE_DROP_ALERT = 0.30  # flag price cuts of 30% or more

# ---------------------------------------------------------------------------
# Source database with trigger-based CDC
# ---------------------------------------------------------------------------
SCHEMA = """
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS cdc_events;

CREATE TABLE products (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL,
    price REAL NOT NULL,
    stock INTEGER NOT NULL
);

CREATE TABLE cdc_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL,
    op         TEXT NOT NULL CHECK (op IN ('INSERT', 'UPDATE', 'DELETE')),
    row_id     INTEGER NOT NULL,
    before     TEXT,
    after      TEXT,
    changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER products_insert AFTER INSERT ON products BEGIN
    INSERT INTO cdc_events (table_name, op, row_id, after)
    VALUES ('products', 'INSERT', NEW.id,
            json_object('name', NEW.name, 'price', NEW.price, 'stock', NEW.stock));
END;

CREATE TRIGGER products_update AFTER UPDATE ON products BEGIN
    INSERT INTO cdc_events (table_name, op, row_id, before, after)
    VALUES ('products', 'UPDATE', NEW.id,
            json_object('name', OLD.name, 'price', OLD.price, 'stock', OLD.stock),
            json_object('name', NEW.name, 'price', NEW.price, 'stock', NEW.stock));
END;

CREATE TRIGGER products_delete AFTER DELETE ON products BEGIN
    INSERT INTO cdc_events (table_name, op, row_id, before)
    VALUES ('products', 'DELETE', OLD.id,
            json_object('name', OLD.name, 'price', OLD.price, 'stock', OLD.stock));
END;
"""


@contextmanager
def connect():
    """Open, commit and always close (an open handle locks the file on Windows)."""
    conn = sqlite3.connect(SHOP_DB)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def reset_shop() -> None:
    # Drop and recreate tables instead of deleting the file, so it works even if the file is open
    SHOP_DB.parent.mkdir(exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


def flag(op: str, before: dict | None, after: dict | None) -> list[str]:
    """Deterministic risk rules. The agent explains these, it doesn't invent them."""
    flags = []
    if op == "DELETE":
        flags.append("product deleted")
    if before and after:
        if after["price"] < before["price"] * (1 - PRICE_DROP_ALERT):
            cut = 1 - after["price"] / before["price"]
            flags.append(f"price cut {cut:.0%}")
        if before["stock"] > 0 and after["stock"] == 0:
            flags.append("went out of stock")
    return flags


# ---------------------------------------------------------------------------
# Tools: consume the change stream
# ---------------------------------------------------------------------------
def fetch_changes(run_context: RunContext, limit: int = 20) -> str:
    """
    Read change events newer than the checkpoint and advance the checkpoint.

    Args:
        limit: Maximum number of events to read

    Returns:
        JSON list of change events, each with op, before, after and flags
    """
    last_seq = run_context.session_state.get("last_seq", 0)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM cdc_events WHERE seq > ? ORDER BY seq LIMIT ?", (last_seq, limit)
        ).fetchall()

    events = []
    for r in rows:
        before = json.loads(r["before"]) if r["before"] else None
        after = json.loads(r["after"]) if r["after"] else None
        events.append({
            "seq": r["seq"], "op": r["op"], "table": r["table_name"], "row_id": r["row_id"],
            "before": before, "after": after, "flags": flag(r["op"], before, after),
        })

    if events:
        run_context.session_state["last_seq"] = events[-1]["seq"]
    needs_attention = [
        {"seq": e["seq"], "row_id": e["row_id"], "flags": e["flags"]} for e in events if e["flags"]
    ]
    return json.dumps({"from_seq": last_seq, "needs_attention": needs_attention, "events": events})


def get_row_history(product_id: int) -> str:
    """
    Get every change event for one product, oldest first.

    Args:
        product_id: The product id

    Returns:
        JSON list of that product's change events
    """
    with connect() as conn:
        rows = conn.execute(
            "SELECT seq, op, before, after, changed_at FROM cdc_events WHERE row_id = ? ORDER BY seq",
            (product_id,),
        ).fetchall()
    return json.dumps([dict(r) for r in rows])


def reset_checkpoint(run_context: RunContext, seq: int = 0) -> str:
    """
    Move the checkpoint back so events are replayed.

    Args:
        seq: Sequence number to rewind to (0 replays everything)

    Returns:
        Confirmation message
    """
    run_context.session_state["last_seq"] = seq
    return f"Checkpoint reset to seq {seq}"


# ---------------------------------------------------------------------------
# Create Agent
# ---------------------------------------------------------------------------
agent = Agent(
    name="CDC Watcher",
    model=Ollama(id="qwen3.5-4b-16k:latest", request_params={"think": False}),
    db=SqliteDb(id="cdc-db", db_file="db/cdc.db"),
    session_state={"last_seq": 0},
    tools=[fetch_changes, get_row_history, reset_checkpoint],
    instructions=[
        "You watch the change stream of the shop's products table.",
        "To process changes, call fetch_changes once, then summarise what changed per product.",
        (
            "Start with a 'Needs attention' section listing every item in needs_attention with its flags "
            "exactly as given; write 'None' only if needs_attention is empty."
        ),
        "Only fetch changes when asked to process them; otherwise don't call fetch_changes.",
        "Never invent changes; if no events are returned, say nothing changed.",
        "Current checkpoint: last processed seq = {last_seq}",
    ],
    markdown=True,
)

# ---------------------------------------------------------------------------
# Run Agent
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    reset_shop()
    # Fresh source DB, so start a fresh consumer session (checkpoint = 0)
    session_id = f"cdc-demo-{uuid4().hex[:8]}"

    # The "application" writes to the database; the triggers capture every change
    with connect() as conn:
        conn.executemany(
            "INSERT INTO products (id, name, price, stock) VALUES (?, ?, ?, ?)",
            [(1, "Mechanical keyboard", 120.0, 40), (2, "USB-C hub", 45.0, 15), (3, "4K monitor", 399.0, 8)],
        )
        conn.execute("UPDATE products SET price = 199.0 WHERE id = 3")  # 50% cut
        conn.execute("UPDATE products SET stock = 0 WHERE id = 2")
        conn.execute("UPDATE products SET price = 115.0 WHERE id = 1")  # small change, no flag

    agent.print_response("Process new changes and flag anything risky.", session_id=session_id)

    # More changes arrive later; only these new events are read
    with connect() as conn:
        conn.execute("DELETE FROM products WHERE id = 2")
        conn.execute("UPDATE products SET stock = 12 WHERE id = 3")

    agent.print_response("Process new changes and flag anything risky.", session_id=session_id)
    agent.print_response("What happened to product 3 over time?", session_id=session_id)

    print(f"Checkpoint: {agent.get_session_state(session_id=session_id)}")
