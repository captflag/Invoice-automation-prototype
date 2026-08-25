#!/usr/bin/env python3
"""Run the whole pipeline on the 20-document sample batch and print what
happened to every invoice. No dependencies beyond the standard library.

    python3 run_demo.py
"""
import json
import os

from orchestrator import Orchestrator

HERE = os.path.dirname(os.path.abspath(__file__))

HUMAN_STATES = {
    "HUMAN_VERIFY_QUEUE", "EXCEPTION_PRICE", "EXCEPTION_QUANTITY", "EXCEPTION_GST",
    "EXCEPTION_UNKNOWN_VENDOR", "EXCEPTION_PO", "PO_SUGGESTED_ONE_CLICK",
    "NON_PO_APPROVAL", "BLOCKED_DUPLICATE", "HELD_NEAR_DUPLICATE",
    "HELD_FRAUD_REVIEW", "NEW_VENDOR_REVIEW",
}


def main():
    with open(os.path.join(HERE, "data", "intake_documents.json")) as f:
        documents = json.load(f)

    orch = Orchestrator()
    items, dropped = orch.process_batch(documents)

    # ---- console report ----------------------------------------------------
    print("=" * 100)
    print("INVOICE AUTOMATION PROTOTYPE — batch run")
    print("=" * 100)
    print(f"\nDropped at intake ({len(dropped)}):")
    for d in dropped:
        print(f"  - {d['filename']}: {d['reason']}")

    print(f"\n{'ID':6} {'Invoice':15} {'Vendor':10} {'Total (Rs)':>12}  Outcome")
    print("-" * 100)
    for w in items:
        print(f"{w.work_id:6} {w.f('invoice_number') or '-':15} {w.vendor_id or '?':10} "
              f"{w.f('total'):>12,.0f}  {w.status}"
              + (f"  -> {w.route_to}" if w.route_to else ""))

    touchless = [w for w in items if w.status == "TOUCHLESS_POSTED"]
    posted = [w for w in items if w.status in
              ("TOUCHLESS_POSTED", "POSTED_AWAITING_PAYMENT_APPROVAL")]
    to_humans = [w for w in items if w.status in HUMAN_STATES or
                 w.status == "DRAFT_AWAITING_TWO_APPROVALS"]

    print("-" * 100)
    print(f"\nSummary: {len(items)} invoices processed"
          f" | touchless: {len(touchless)} ({len(touchless)/len(items):.0%})"
          f" | posted to ERP: {len(posted)}"
          f" | routed to a person: {len(to_humans)}")
    print(f"Audit log: {len(orch.audit.entries)} entries, hash chain intact: "
          f"{orch.audit.verify_chain()}")
    print(f"ERP permission denials (SoD self-test): {len(orch.erp.permission_denials)} "
          f"forbidden actions attempted by the agent role, all refused by the ERP")
    print("\nInvariant check: no invoice with a failed check reached a payment state ->",
          all(w.payment_state is None for w in items if w.exceptions))

    # ---- artifacts for the dashboard --------------------------------------
    out_dir = os.path.join(HERE, "out")
    os.makedirs(out_dir, exist_ok=True)
    results = orch.results(items, dropped)
    with open(os.path.join(out_dir, "results.json"), "w") as f:
        json.dump(results, f, indent=2)
    orch.audit.dump(os.path.join(out_dir, "audit_log.jsonl"))
    print(f"\nWrote out/results.json and out/audit_log.jsonl")


if __name__ == "__main__":
    main()
