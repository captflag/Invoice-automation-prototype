"""Posting Agent — writes to the ERP as `invoice-clerk`, a role that can create
drafts and post matched invoices but can NOT approve payments, release payment
runs, or change bank details. Those refusals are the ERP's, not the agent's
good manners (see erp_mock.MockERP).

Draft by default. Posts directly only when every check has passed AND the
amount sits inside the DoA gate for touchless handling. Every write carries an
idempotency key, so a retried call cannot create the same invoice twice."""
from config import DOA, NEW_VENDOR_PROBATION, AGENT_ERP_ROLE


class PostingAgent:
    def __init__(self, erp, audit):
        self.erp = erp
        self.audit = audit

    def run(self, wi):
        total = wi.f("total")
        payload = {
            "vendor_id": wi.vendor_id,
            "invoice_number": wi.f("invoice_number"),
            "po_number": wi.f("po_number"),
            "total": total,
            "lines": [{"item_code": l["item_code"].value, "qty": l["qty"].value} for l in wi.lines],
        }
        idem_key = f"{wi.vendor_id}:{wi.f('invoice_number')}:{total}"

        # Anomaly gate: a clean match with a fraud flag still goes to a person.
        if wi.anomaly_flags:
            wi.erp_ref = self.erp.create_invoice(AGENT_ERP_ROLE, idem_key, payload, post=False)
            wi.status, wi.queue, wi.route_to = "HELD_FRAUD_REVIEW", "fraud-review", "ap-manager"
            wi.notes.append("All checks passed on the DATA, but the document raised an anomaly flag — "
                            "parked as draft for fraud review, not posted.")
            self.audit.log("posting-agent", wi.work_id, "DRAFT_HELD_FRAUD_REVIEW",
                           f"{wi.erp_ref}: {wi.anomaly_flags[0][:80]}")
            return wi

        # New-vendor probation: first few invoices are always checked by a person.
        vendor = self.erp.vendors[wi.vendor_id]
        if vendor["invoices_processed"] < NEW_VENDOR_PROBATION:
            wi.erp_ref = self.erp.create_invoice(AGENT_ERP_ROLE, idem_key, payload, post=False)
            wi.status, wi.queue, wi.route_to = "NEW_VENDOR_REVIEW", "ap-review", "ap-clerk"
            wi.notes.append(f"New vendor ({vendor['invoices_processed']} invoices processed, "
                            f"probation is {NEW_VENDOR_PROBATION}): clean match, but a person "
                            f"reviews anyway. Bank details were verified at onboarding by callback.")
            self.audit.log("posting-agent", wi.work_id, "DRAFT_NEW_VENDOR_REVIEW", wi.erp_ref)
            return wi

        if total <= DOA["auto_post_limit"]:
            wi.erp_ref = self.erp.create_invoice(AGENT_ERP_ROLE, idem_key, payload, post=True)
            wi.status = "TOUCHLESS_POSTED"
            wi.payment_state = "IN_NEXT_PAYMENT_RUN (a human releases every run)"
            self.audit.log("posting-agent", wi.work_id, "POSTED_TOUCHLESS",
                           f"{wi.erp_ref}, Rs {total:,.0f} <= auto-post limit")
        elif total <= DOA["manager_approval_limit"]:
            wi.erp_ref = self.erp.create_invoice(AGENT_ERP_ROLE, idem_key, payload, post=True)
            wi.status, wi.queue, wi.route_to = "POSTED_AWAITING_PAYMENT_APPROVAL", "manager-approvals", "ap-manager"
            wi.payment_state = "BLOCKED_UNTIL_MANAGER_APPROVES"
            self.audit.log("posting-agent", wi.work_id, "POSTED_NEEDS_MANAGER",
                           f"{wi.erp_ref}, Rs {total:,.0f} needs one approval")
        else:
            wi.erp_ref = self.erp.create_invoice(AGENT_ERP_ROLE, idem_key, payload, post=False)
            wi.status, wi.queue, wi.route_to = "DRAFT_AWAITING_TWO_APPROVALS", "dual-approvals", "ap-manager+finance-head"
            wi.payment_state = "BLOCKED_UNTIL_TWO_APPROVALS"
            wi.notes.append("Large invoices always need two approvers, no matter how clean the match.")
            self.audit.log("posting-agent", wi.work_id, "DRAFT_NEEDS_TWO_APPROVERS",
                           f"{wi.erp_ref}, Rs {total:,.0f}")
        return wi
