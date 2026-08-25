"""Communication Agent — drafts every outward or internal message. Where money
or disputes are involved, drafts are for HUMAN SEND: the agent never sends on
its own in this prototype, it only prepares."""


class CommunicationAgent:
    def __init__(self, audit):
        self.audit = audit

    def _draft(self, wi, to, subject, body):
        draft = {"to": to, "subject": subject, "body": body, "requires_human_send": True}
        wi.drafts.append(draft)
        self.audit.log("communication-agent", wi.work_id, "MESSAGE_DRAFTED",
                       f"to {to}: {subject}")
        return draft

    def draft_supplier_notice(self, wi, subject, body):
        return self._draft(wi, f"supplier:{wi.f('vendor_name')}", subject, body)

    def draft_internal(self, wi, owner, body):
        return self._draft(wi, f"internal:{owner}", f"Action needed on {wi.f('invoice_number')}", body)
