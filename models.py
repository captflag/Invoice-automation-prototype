"""Data shapes shared by all agents."""
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Field:
    """One extracted field. The LLM's only legal output shape: a value, a
    confidence, and where it came from. No free text ever leaves extraction."""
    value: Any
    confidence: float
    source: str  # "e-invoice-qr" | "pdf-text" | "ocr-vlm"

    def to_dict(self):
        return asdict(self)


@dataclass
class WorkItem:
    """One invoice moving through the pipeline. Created by the Intake Agent,
    finished when it lands in a terminal state."""
    work_id: str
    doc_id: str
    channel: str
    filename: str
    fields: dict = field(default_factory=dict)        # name -> Field
    lines: list = field(default_factory=list)          # list[dict[name -> Field]]
    vendor_id: Optional[str] = None
    po_type: Optional[str] = None                      # "goods" | "services"
    status: str = "NEW"
    queue: Optional[str] = None                        # which human queue, if any
    route_to: Optional[str] = None                     # who owns the next action
    checks: list = field(default_factory=list)         # every check run + outcome
    exceptions: list = field(default_factory=list)     # typed exceptions from triage
    notes: list = field(default_factory=list)
    drafts: list = field(default_factory=list)         # messages drafted (never sent) by Communication Agent
    erp_ref: Optional[str] = None
    payment_state: Optional[str] = None
    anomaly_flags: list = field(default_factory=list)

    def f(self, name, default=None):
        fld = self.fields.get(name)
        return fld.value if fld else default

    def add_check(self, name, passed, detail=""):
        self.checks.append({"check": name, "passed": passed, "detail": detail})
        return passed

    def to_dict(self):
        d = asdict(self)
        return d
