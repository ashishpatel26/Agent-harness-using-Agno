"""
Deterministic Tax Engine
========================
The single source of truth for every number in this project.

Agents never calculate tax. They interpret requests, pick which engine function
to call, and explain the result. Every rate, taxability decision, rounding rule,
certificate check and reconciliation lives here as plain, testable Python.

DEMO DATA ONLY: state base rates, no local/district rates, simplified
taxability. Do not use for real filings.

Run `python tax_engine.py` to execute the self-check.
"""

import csv
import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CENT = Decimal("0.01")

# State base rates (percent). Simplified: no county/city/district add-ons.
STATE_RATES = {
    "CA": Decimal("7.25"),
    "NY": Decimal("4.00"),
    "TX": Decimal("6.25"),
    "WA": Decimal("6.50"),
    "FL": Decimal("6.00"),
    "OR": Decimal("0.00"),
}

# Tax code catalog: the only codes the Classification Agent may assign.
TAX_CODES = {
    "TPP-GEN": "General tangible personal property",
    "SW-SAAS": "Software as a service (remote access)",
    "DIG-DL": "Digitally downloaded goods (software, e-books, media)",
    "FOOD-GROC": "Grocery food for home consumption",
    "CLOTH-GEN": "General clothing and footwear",
    "SVC-PRO": "Professional services (consulting, legal, accounting)",
}

# Taxability matrix: which (state, code) pairs are exempt. Anything absent is taxable.
EXEMPT = {
    ("CA", "SW-SAAS"), ("CA", "DIG-DL"), ("CA", "FOOD-GROC"), ("CA", "SVC-PRO"),
    ("NY", "FOOD-GROC"), ("NY", "SVC-PRO"),
    ("TX", "FOOD-GROC"), ("TX", "SVC-PRO"),
    ("WA", "FOOD-GROC"),
    ("FL", "SW-SAAS"), ("FL", "DIG-DL"), ("FL", "FOOD-GROC"), ("FL", "SVC-PRO"),
}


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _check_state(state: str) -> str:
    state = state.upper().strip()
    if state not in STATE_RATES:
        raise ValueError(f"Unsupported jurisdiction '{state}'. Supported: {', '.join(STATE_RATES)}")
    return state


def is_valid_tax_code(code: str) -> bool:
    return code in TAX_CODES


# ---------------------------------------------------------------------------
# Exemption certificates
# ---------------------------------------------------------------------------
def load_certificates() -> dict:
    return json.loads((DATA_DIR / "certificates.json").read_text())


def validate_certificate(cert_id: str, state: str, on: date | None = None) -> dict:
    """Check a certificate exists, covers the state, and is not expired."""
    state = _check_state(state)
    on = on or date.today()
    cert = load_certificates().get(cert_id)
    if cert is None:
        return {"cert_id": cert_id, "valid": False, "reason": "Certificate not on file"}
    if state not in cert["states"]:
        return {"cert_id": cert_id, "valid": False, "reason": f"Certificate does not cover {state}"}
    if date.fromisoformat(cert["expires"]) < on:
        return {"cert_id": cert_id, "valid": False, "reason": f"Certificate expired on {cert['expires']}"}
    return {"cert_id": cert_id, "valid": True, "customer": cert["customer"], "reason": cert["reason"]}


# ---------------------------------------------------------------------------
# Calculation
# ---------------------------------------------------------------------------
def calculate(lines: list[dict], ship_to_state: str, cert_id: str | None = None) -> dict:
    """
    Calculate sales tax for an order.

    lines: [{"description": str, "tax_code": str, "amount": number}]
    Tax is rounded per line, half-up, to the cent.
    """
    state = _check_state(ship_to_state)
    exemption = validate_certificate(cert_id, state) if cert_id else None
    exempt_by_cert = bool(exemption and exemption["valid"])
    rate = STATE_RATES[state]

    out_lines, total_tax, subtotal = [], Decimal("0"), Decimal("0")
    for line in lines:
        code = line["tax_code"]
        if not is_valid_tax_code(code):
            raise ValueError(f"Unknown tax code '{code}'")
        amount = _money(line["amount"])
        if exempt_by_cert:
            taxable, reason = False, f"exempt by certificate {cert_id}"
        elif (state, code) in EXEMPT:
            taxable, reason = False, f"{code} is exempt in {state}"
        else:
            taxable, reason = True, f"{code} is taxable in {state} at {rate}%"
        tax = _money(amount * rate / 100) if taxable else Decimal("0.00")
        subtotal += amount
        total_tax += tax
        out_lines.append({
            "description": line.get("description", code),
            "tax_code": code,
            "amount": str(amount),
            "taxable": taxable,
            "tax": str(tax),
            "reason": reason,
        })

    return {
        "ship_to_state": state,
        "rate_percent": str(rate),
        "lines": out_lines,
        "subtotal": str(subtotal),
        "total_tax": str(total_tax),
        "grand_total": str(subtotal + total_tax),
        "exemption": exemption,
        "engine": "deterministic-demo-v1",
    }


# ---------------------------------------------------------------------------
# Reconciliation and returns
# ---------------------------------------------------------------------------
def _read_csv(name: str) -> list[dict]:
    with open(DATA_DIR / name, newline="") as f:
        return list(csv.DictReader(f))


def reconcile(period: str) -> dict:
    """
    Compare ERP invoices for a period (YYYY-MM) against the engine and the tax log.

    Flags: under-collected, over-collected, missing from tax log, unknown tax code.
    """
    invoices = [r for r in _read_csv("erp_invoices.csv") if r["date"].startswith(period)]
    logged = {r["invoice_id"]: r for r in _read_csv("tax_log.csv")}

    discrepancies = []
    for inv in invoices:
        if not is_valid_tax_code(inv["tax_code"]):
            discrepancies.append({"invoice_id": inv["invoice_id"], "type": "unknown_tax_code", "state": inv["state"],
                                  "detail": f"'{inv['tax_code']}' is not in the catalog"})
            continue
        expected = Decimal(calculate(
            [{"tax_code": inv["tax_code"], "amount": inv["amount"]}],
            inv["state"], inv["cert_id"] or None,
        )["total_tax"])
        charged = _money(inv["tax_charged"])
        if charged != expected:
            kind = "under_collected" if charged < expected else "over_collected"
            discrepancies.append({"invoice_id": inv["invoice_id"], "type": kind, "state": inv["state"],
                                  "charged": str(charged), "expected": str(expected),
                                  "difference": str(expected - charged)})
        if inv["invoice_id"] not in logged:
            discrepancies.append({"invoice_id": inv["invoice_id"], "type": "missing_from_tax_log", "state": inv["state"],
                                  "detail": "Invoice is in the ERP but was never reported to the tax log"})

    return {"period": period, "invoices_checked": len(invoices),
            "discrepancies": discrepancies, "clean": not discrepancies}


def filing_summary(period: str, state: str) -> dict:
    """Liability for one state and period, from the tax log (what was reported)."""
    state = _check_state(state)
    rows = [r for r in _read_csv("tax_log.csv") if r["date"].startswith(period) and r["state"] == state]
    gross = sum((_money(r["amount"]) for r in rows), Decimal("0"))
    tax = sum((_money(r["tax"]) for r in rows), Decimal("0"))
    open_issues = [d for d in reconcile(period)["discrepancies"] if d["state"] == state]
    return {"period": period, "state": state, "transactions": len(rows),
            "gross_sales": str(gross), "tax_liability": str(tax),
            "open_discrepancies": len(open_issues), "ready_to_file": not open_issues}


# ---------------------------------------------------------------------------
# Self-check: the smallest thing that fails if the engine breaks
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    r = calculate([{"tax_code": "TPP-GEN", "amount": 100}], "CA")
    assert r["total_tax"] == "7.25", r
    r = calculate([{"tax_code": "SW-SAAS", "amount": 100}], "CA")
    assert r["total_tax"] == "0.00", "SaaS is exempt in CA"
    r = calculate([{"tax_code": "SW-SAAS", "amount": 100}], "NY")
    assert r["total_tax"] == "4.00", r
    r = calculate([{"tax_code": "TPP-GEN", "amount": "10.10"}], "TX")
    assert r["total_tax"] == "0.63", "half-up rounding: 0.63125 -> 0.63"
    assert validate_certificate("EX-1001", "TX", date(2026, 1, 1))["valid"]
    assert not validate_certificate("EX-1002", "TX", date(2026, 1, 1))["valid"], "EX-1002 expired"
    assert not validate_certificate("EX-1001", "CA", date(2026, 1, 1))["valid"], "EX-1001 is TX/WA only"
    try:
        calculate([{"tax_code": "MADE-UP", "amount": 1}], "CA")
        raise AssertionError("unknown code must raise")
    except ValueError:
        pass
    rec = reconcile("2026-09")
    kinds = sorted(d["type"] for d in rec["discrepancies"])
    assert kinds == ["missing_from_tax_log", "over_collected", "under_collected", "unknown_tax_code"], kinds
    assert not filing_summary("2026-09", "TX")["ready_to_file"], "TX has an under-collection"
    assert filing_summary("2026-09", "NY")["ready_to_file"], "NY is clean"
    print("tax_engine self-check passed")
    print(json.dumps(rec, indent=2))
