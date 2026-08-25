"""Orchestrator — one queue per invoice, deterministic control flow.

The flow mirrors Figure 1 of the case study:

  intake -> extraction -> validation -> matching -> posting
                             |             |
                             +--> triage <-+       (any failed check)

Two invariants hold everywhere:
  * every failure path routes to a PERSON — no failure path routes to payment;
  * the agents' ERP role cannot approve, pay, or touch bank details, and the
    ERP (not the prompt) enforces that.
"""
import json

from erp_mock import MockERP, ERPPermissionError
from config import AUDIT_SAMPLE_RATE, AGENT_ERP_ROLE
from agents.intake_agent import IntakeAgent
from agents.extraction_agent import ExtractionAgent
from agents.validation_agent import ValidationAgent
from agents.matching_agent import MatchingAgent
from agents.triage_agent import TriageAgent
from agents.posting_agent import PostingAgent
from agents.audit_agent import AuditAgent
from agents.communication_agent import CommunicationAgent


class Orchestrator:
    def __init__(self):
        self.erp = MockERP()
        self.audit = AuditAgent()
        self.comms = CommunicationAgent(self.audit)
        self.intake = IntakeAgent(self.audit)
        self.extraction = ExtractionAgent(self.audit)
        self.validation = ValidationAgent(self.erp, self.audit)
        self.matching = MatchingAgent(self.erp, self.audit)
        self.triage = TriageAgent(self.erp, self.audit, self.comms)
        self.posting = PostingAgent(self.erp, self.audit)

    def process_batch(self, documents):
        work_items, dropped = self.intake.run(documents)
        finished = []
        for wi, doc in work_items:
            self.extraction.run(wi, doc)
            self.validation.run(wi)
            if wi.exceptions:
                self.triage.run(wi)
                finished.append(wi)
                continue
            self.matching.run(wi)
            if wi.exceptions:
                self.triage.run(wi)
                finished.append(wi)
                continue
            self.posting.run(wi)
            finished.append(wi)

        self._continuous_controls(finished)
        self._sod_self_test()
        return finished, dropped

    def _continuous_controls(self, items):
        """The 2% value-weighted audit of touchless invoices — the one control
        that proves the system still deserves its freedom. With a small batch
        this floors at one invoice per day: the single highest-value one."""
        touchless = [w for w in items if w.status == "TOUCHLESS_POSTED"]
        n = max(1, round(len(touchless) * AUDIT_SAMPLE_RATE)) if touchless else 0
        for w in sorted(touchless, key=lambda x: -x.f("total"))[:n]:
            w.notes.append("Selected for today's value-weighted human audit sample.")
            self.audit.log("continuous-controls", w.work_id, "AUDIT_SAMPLE_SELECTED",
                           f"Rs {w.f('total'):,.0f} — highest-value touchless invoice")

    def _sod_self_test(self):
        """Prove (not promise) the separation of duties: the agents' own ERP
        role attempts the three forbidden actions; the ERP refuses each one."""
        for action, call in [
            ("approve a payment", lambda: self.erp.approve_payment(AGENT_ERP_ROLE, "ERP-DOC-5001")),
            ("release the payment run", lambda: self.erp.release_payment_run(AGENT_ERP_ROLE)),
            ("change vendor bank details", lambda: self.erp.change_bank_details(
                AGENT_ERP_ROLE, "V-1001", "ATTACKER-ACCT-0001")),
        ]:
            try:
                call()
                raise AssertionError(f"SoD BREACH: agent role was allowed to {action}")
            except ERPPermissionError as e:
                self.audit.log("sod-self-test", None, "PERMISSION_REFUSED_AS_DESIGNED",
                               f"agent tried to {action}: {e}")

    def results(self, items, dropped):
        return {
            "dropped_at_intake": dropped,
            "invoices": [w.to_dict() for w in items],
            "audit_log": self.audit.entries,
            "audit_chain_intact": self.audit.verify_chain(),
            "erp_permission_denials": self.erp.permission_denials,
        }
