"""Mock ERP (stands in for SAP / Oracle / Dynamics).

Two properties matter and both are enforced HERE, not in agent code:

1. Permissions. Every call carries a role, and the ERP refuses anything the
   role doesn't allow. The agents run as `invoice-clerk`, which cannot approve,
   cannot pay, and cannot touch bank details — so even a fully compromised
   agent (see the prompt-injection fixture, D17) cannot move money.

2. Idempotency. Every write carries a unique key. A retried call with the same
   key returns the original result instead of creating a second invoice.
"""
import json
import os
from config import ROLE_PERMISSIONS

DATA = os.path.join(os.path.dirname(__file__), "data")


class ERPPermissionError(Exception):
    pass


class MockERP:
    def __init__(self):
        self.vendors = {v["vendor_id"]: v for v in self._load("vendors.json")}
        self.pos = {p["po_number"]: p for p in self._load("purchase_orders.json")}
        self.grns = self._load("goods_receipts.json")
        self.invoice_register = self._load("invoice_register.json")
        self._idempotency = {}          # key -> result of the first call
        self._doc_counter = 5000
        self.posted = []                # invoices created this run
        self.permission_denials = []    # every refused call, for the audit trail

    def _load(self, name):
        with open(os.path.join(DATA, name)) as f:
            return json.load(f)

    def _require(self, role, permission):
        if permission not in ROLE_PERMISSIONS.get(role, set()):
            self.permission_denials.append({"role": role, "attempted": permission})
            raise ERPPermissionError(
                f"role '{role}' does not hold permission '{permission}' — refused by ERP, "
                f"regardless of what any agent or document says")

    # ---- reads (clerk role is allowed these) ------------------------------
    def find_vendor_by_gstin(self, gstin):
        matches = [v for v in self.vendors.values() if v["gstin"] == gstin]
        return matches

    def get_po(self, po_number):
        return self.pos.get(po_number)

    def grns_for_po(self, po_number):
        return [g for g in self.grns if g["po_number"] == po_number]

    def register_lookup(self, vendor_id, invoice_number=None):
        rows = [r for r in self.invoice_register if r["vendor_id"] == vendor_id]
        if invoice_number is not None:
            rows = [r for r in rows if r["invoice_number"] == invoice_number]
        return rows

    def search_pos_for_vendor(self, vendor_id):
        return [p for p in self.pos.values() if p["vendor_id"] == vendor_id]

    def already_invoiced_qty(self, po_number, item_code):
        """Cumulative quantity already billed against a PO line (from register +
        anything posted this run). Kept simple for the demo."""
        qty = 0
        for inv in self.posted:
            if inv.get("po_number") == po_number:
                for ln in inv.get("lines", []):
                    if ln.get("item_code") == item_code:
                        qty += ln.get("qty", 0)
        return qty

    # ---- writes (permission-gated) ----------------------------------------
    def create_invoice(self, role, idempotency_key, payload, post=False):
        """Create an invoice document. `post=False` -> parked draft (the default).
        `post=True` needs post_invoice permission and is only called by the
        Posting Agent after every check has passed."""
        self._require(role, "post_invoice" if post else "create_draft")
        if idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]  # retry-safe: no double entry
        self._doc_counter += 1
        ref = f"ERP-DOC-{self._doc_counter}"
        record = dict(payload, erp_ref=ref, state="POSTED" if post else "DRAFT")
        self.posted.append(record)
        self._idempotency[idempotency_key] = ref
        return ref

    def approve_payment(self, role, erp_ref):
        self._require(role, "approve_payment")
        return f"payment for {erp_ref} approved"

    def release_payment_run(self, role):
        self._require(role, "release_payment_run")
        return "payment run released"

    def change_bank_details(self, role, vendor_id, new_account):
        self._require(role, "change_bank_details")
        return "bank details changed"
