"""Exception Triage Agent — works out what went wrong, drafts the fix, and
routes it to the right OWNER. Invoices with exceptions sit parked: no failure
path routes to payment."""


class TriageAgent:
    def __init__(self, erp, audit, comms):
        self.erp = erp
        self.audit = audit
        self.comms = comms

    def run(self, wi):
        # Highest-severity exception decides the queue; all are kept on the item.
        order = ["DUPLICATE_BLOCK", "NEAR_DUPLICATE", "UNKNOWN_VENDOR", "GST_CHECKSUM_FAIL",
                 "GST_TYPE_FAIL", "GST_MATH_FAIL", "ARITHMETIC_FAIL", "LOW_CONFIDENCE",
                 "PRICE_MISMATCH", "QTY_OVER_RECEIPT", "MISSING_PO", "PO_VENDOR_MISMATCH",
                 "LINE_UNMATCHED"]
        primary = sorted(wi.exceptions, key=lambda e: order.index(e["type"]))[0]
        t, detail = primary["type"], primary["detail"]

        if t == "DUPLICATE_BLOCK":
            wi.status, wi.queue, wi.route_to = "BLOCKED_DUPLICATE", "ap-duplicates", "accounts-payable"
            self.comms.draft_supplier_notice(wi, "Duplicate invoice",
                f"Invoice {wi.f('invoice_number')} was already paid. No action needed; "
                f"contact AP if you believe this is an error.")

        elif t == "NEAR_DUPLICATE":
            wi.status, wi.queue, wi.route_to = "HELD_NEAR_DUPLICATE", "ap-duplicates", "accounts-payable"
            wi.notes.append("Held, not rejected: might be a corrected resubmission. "
                            "A person decides — never auto-rejected, never auto-paid.")

        elif t == "UNKNOWN_VENDOR":
            wi.status, wi.queue, wi.route_to = "EXCEPTION_UNKNOWN_VENDOR", "procurement", "procurement"
            wi.notes.append("Vendor is NOT auto-created. Procurement onboards (with bank-detail "
                            "verification) or rejects.")

        elif t.startswith("GST"):
            wi.status, wi.queue, wi.route_to = "EXCEPTION_GST", "ap-tax", "accounts-payable"
            self.comms.draft_supplier_notice(wi, "GST discrepancy on your invoice",
                f"Invoice {wi.f('invoice_number')}: {detail}. Please issue a corrected invoice.")

        elif t in ("ARITHMETIC_FAIL", "LOW_CONFIDENCE"):
            wi.status, wi.queue, wi.route_to = "HUMAN_VERIFY_QUEUE", "human-verify", "ap-clerk"
            wi.notes.append("Person sees the invoice image side-by-side with extracted data; "
                            "their correction goes back into model training.")

        elif t == "PRICE_MISMATCH":
            wi.status, wi.queue, wi.route_to = "EXCEPTION_PRICE", "buyer-review", "buyer"
            self.comms.draft_internal(wi, "buyer",
                f"Price gap on {wi.f('invoice_number')} vs {wi.f('po_number')}: {detail}. "
                f"Approve a PO price change or ask the vendor for a credit note. "
                f"Invoice is parked and cannot be paid until resolved.")

        elif t == "QTY_OVER_RECEIPT":
            wi.status, wi.queue, wi.route_to = "EXCEPTION_QUANTITY", "warehouse-review", "warehouse"
            self.comms.draft_internal(wi, "warehouse",
                f"{wi.f('invoice_number')}: {detail}. Confirm whether goods arrived unrecorded "
                f"(book the GRN) or short (vendor to issue credit note). Invoice parked meanwhile.")

        elif t == "MISSING_PO":
            self._missing_po(wi)

        else:  # PO_VENDOR_MISMATCH, LINE_UNMATCHED
            wi.status, wi.queue, wi.route_to = "EXCEPTION_PO", "ap-review", "accounts-payable"

        self.audit.log("triage-agent", wi.work_id, wi.status,
                       f"{t} -> queue '{wi.queue}' (owner: {wi.route_to}); fix drafted where applicable")
        return wi

    def _missing_po(self, wi):
        """Search likely POs by vendor + amount. One strong match becomes a
        one-click confirmation; no match routes as a non-PO invoice."""
        cands = []
        for po in self.erp.search_pos_for_vendor(wi.vendor_id):
            po_total = sum(l["qty_ordered"] * l["unit_price"] for l in po["lines"])
            if abs(po_total - wi.f("subtotal")) / max(po_total, 1) < 0.02:
                cands.append(po["po_number"])
        if len(cands) == 1:
            wi.status, wi.queue, wi.route_to = "PO_SUGGESTED_ONE_CLICK", "ap-confirm", "ap-clerk"
            wi.notes.append(f"Strong match found: {cands[0]} (same vendor, amount within 2%). "
                            f"One click confirms; the agent never self-confirms its own guess.")
        else:
            wi.status, wi.queue, wi.route_to = "NON_PO_APPROVAL", "requester-approval", "requester"
            wi.notes.append("No PO match — routed to whoever made the purchase for normal non-PO approval.")
            self.comms.draft_supplier_notice(wi, "PO number required",
                "Reminder: invoices without a PO number will not be paid. "
                "Please quote the PO on future invoices.")
