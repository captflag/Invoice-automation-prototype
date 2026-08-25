"""Extraction Agent — the three-step ladder from the case study.

  Tier 1: signed e-invoice QR  -> government-verified fields, confidence 1.0
  Tier 2: digital PDF text     -> direct text read, no OCR, high confidence
  Tier 3: scan/photo           -> OCR + vision model, per-field confidence

THIS IS THE MOCK SEAM. In production, `_read_tier2` and `_read_tier3` are the
calls to a vision/LLM extraction model. The contract is the important part and
it is identical here and in production:

  * the model may ONLY return {field: (value, confidence)} — never free text,
    never an instruction, never a decision;
  * anything in the document that is not an invoice field (including text that
    talks TO the system) is discarded and reported as an anomaly, because
    documents are data, not instructions.

The mock replays each fixture's `printed` fields with tier-appropriate
confidence, and injects the fixture's declared defects (misreads, blur) so the
downstream checks have something real to catch.
"""
from models import Field

TIER_SOURCE = {1: "e-invoice-qr", 2: "pdf-text", 3: "ocr-vlm"}
TIER_BASE_CONF = {1: 1.0, 2: 0.99, 3: 0.94}

HEADER_FIELDS = ["vendor_name", "vendor_gstin", "invoice_number", "invoice_date",
                 "po_number", "payment_terms", "currency",
                 "subtotal", "cgst", "sgst", "igst", "total"]
LINE_FIELDS = ["item_code", "description", "qty", "unit_price", "line_total"]


class ExtractionAgent:
    def __init__(self, audit):
        self.audit = audit

    def run(self, wi, doc):
        tier = doc["quality_tier"]
        src, base = TIER_SOURCE[tier], TIER_BASE_CONF[tier]
        printed = doc["printed"]

        for name in HEADER_FIELDS:
            wi.fields[name] = Field(printed[name], base, src)
        for line in printed["lines"]:
            wi.lines.append({n: Field(line[n], base, src) for n in LINE_FIELDS})

        # Tier 1: the QR-signed identity/amount fields are government records —
        # certainty, not model output. Line detail still comes from the PDF text.
        if tier == 1:
            for name in ["vendor_gstin", "invoice_number", "invoice_date",
                         "subtotal", "cgst", "sgst", "igst", "total"]:
                wi.fields[name] = Field(printed[name], 1.0, "e-invoice-qr")
            for ln in wi.lines:
                for n in LINE_FIELDS:
                    ln[n] = Field(ln[n].value, 0.99, "pdf-text")

        # Replay the fixture's defects — the stand-in for real OCR errors.
        for defect in doc["defects"]:
            if defect["type"] == "low_confidence":
                f = wi.fields[defect["field"]]
                wi.fields[defect["field"]] = Field(f.value, defect["confidence"], src)
            elif defect["type"] == "misread":
                # e.g. "lines[1].line_total" — the model read a wrong number and
                # is fairly sure about it. Only arithmetic will catch this.
                idx = int(defect["field"].split("[")[1].split("]")[0])
                fname = defect["field"].split(".")[1]
                wi.lines[idx][fname] = Field(defect["read_as"], defect["confidence"], src)
            elif defect["type"] == "hidden_text":
                # The document tried to talk to the system. The extraction
                # contract has no output slot for instructions, so the text is
                # dropped — and its presence is itself a fraud signal.
                wi.anomaly_flags.append(
                    "document contained instruction-like text addressed to the AI; "
                    "discarded (documents are data, not instructions) and flagged for fraud review")
                self.audit.log("extraction-agent", wi.work_id, "INJECTION_ATTEMPT_DISCARDED",
                               f"hidden text began: {defect['text'][:60]!r}...")

        wi.status = "EXTRACTED"
        low = [n for n, f in wi.fields.items() if f.value is not None and f.confidence < 1.0]
        self.audit.log("extraction-agent", wi.work_id, "EXTRACTED",
                       f"tier {tier} ({src}), {len(wi.lines)} lines, "
                       f"min field confidence {min((f.confidence for f in wi.fields.values()), default=1.0):.2f}")
        return wi
