"""Audit Agent — a permanent, tamper-evident log of everything the others did.

Each entry carries the SHA-256 hash of the previous entry, so editing any past
entry breaks the chain from that point on. `verify_chain()` proves integrity.
(Timestamps are a logical sequence here so demo runs are reproducible.)"""
import hashlib
import json


class AuditAgent:
    def __init__(self):
        self.entries = []
        self._prev_hash = "GENESIS"

    def log(self, actor, work_id, event, detail=""):
        entry = {
            "seq": len(self.entries) + 1,
            "actor": actor,
            "work_id": work_id,
            "event": event,
            "detail": detail,
            "prev_hash": self._prev_hash,
        }
        entry["hash"] = hashlib.sha256(
            json.dumps(entry, sort_keys=True).encode()).hexdigest()[:16]
        self._prev_hash = entry["hash"]
        self.entries.append(entry)
        return entry

    def verify_chain(self):
        prev = "GENESIS"
        for e in self.entries:
            body = {k: v for k, v in e.items() if k != "hash"}
            if e["prev_hash"] != prev or e["hash"] != hashlib.sha256(
                    json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]:
                return False
            prev = e["hash"]
        return True

    def dump(self, path):
        with open(path, "w") as f:
            for e in self.entries:
                f.write(json.dumps(e) + "\n")
