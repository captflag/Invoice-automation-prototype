"""Intake Agent — watches all four channels, throws out junk and duplicate
files, and creates exactly one work item per invoice."""
from models import WorkItem


class IntakeAgent:
    def __init__(self, audit):
        self.audit = audit
        self._seen_hashes = {}
        self._counter = 0

    def run(self, documents):
        work_items, dropped = [], []
        for doc in documents:
            if doc["junk"]:
                dropped.append({"doc_id": doc["doc_id"], "filename": doc["filename"],
                                "reason": "classified as non-invoice (junk) and discarded"})
                self.audit.log("intake-agent", None, "DOC_DISCARDED_JUNK",
                               f"{doc['filename']} is not an invoice")
                continue
            if doc["file_hash"] in self._seen_hashes:
                first = self._seen_hashes[doc["file_hash"]]
                dropped.append({"doc_id": doc["doc_id"], "filename": doc["filename"],
                                "reason": f"duplicate file (same hash as {first}) — dropped at intake"})
                self.audit.log("intake-agent", None, "DOC_DISCARDED_DUP_FILE",
                               f"{doc['filename']} duplicates {first}")
                continue
            self._seen_hashes[doc["file_hash"]] = doc["doc_id"]
            self._counter += 1
            wi = WorkItem(work_id=f"WI-{self._counter:03d}", doc_id=doc["doc_id"],
                          channel=doc["channel"], filename=doc["filename"])
            wi.status = "INTAKE_OK"
            self.audit.log("intake-agent", wi.work_id, "WORK_ITEM_CREATED",
                           f"{doc['filename']} via {doc['channel']}")
            work_items.append((wi, doc))
        return work_items, dropped
