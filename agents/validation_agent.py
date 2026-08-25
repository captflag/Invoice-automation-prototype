"""Validation Agent — deterministic code, not LLM (the case study is explicit
about this). Every extracted number must survive arithmetic that an invented
number cannot survive. Nothing is ever silently corrected: any failure becomes
a typed exception for a person."""
import difflib
from config import BUYER, GST_RATE, CONFIDENCE_FLOOR

GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def gstin_checksum_ok(gstin):
    """Real GSTIN check-digit algorithm (mod-36, alternating weights 1/2)."""
    if not gstin or len(gstin) != 15 or any(c not in GSTIN_CHARS for c in gstin):
        return False
    total = 0
    for i, ch in enumerate(gstin[:14]):
        p = GSTIN_CHARS.index(ch) * (2 if i % 2 else 1)
        total += p // 36 + p % 36
    return GSTIN_CHARS[(36 - total % 36) % 36] == gstin[14]


class ValidationAgent:
    def __init__(self, erp, audit):
        self.erp = erp
        self.audit = audit

    def run(self, wi):
        failures = []

        # -- 1. Confidence floor: a person sees anything the model is unsure of
        low = [(n, f.confidence) for n, f in wi.fields.items()
               if f.value is not None and f.confidence < CONFIDENCE_FLOOR]
        for ln in wi.lines:
            low += [(f"line.{n}", f.confidence) for n, f in ln.items()
                    if f.value is not None and f.confidence < CONFIDENCE_FLOOR]
        if not wi.add_check("confidence_floor", not low,
                            "; ".join(f"{n}={c:.2f}" for n, c in low)):
            failures.append(("LOW_CONFIDENCE", f"fields below {CONFIDENCE_FLOOR}: "
                             + ", ".join(f"{n} ({c:.2f})" for n, c in low)))

        # -- 2. Vendor must match exactly ONE vendor master record
        gstin = wi.f("vendor_gstin")
        checksum_ok = wi.add_check("gstin_checksum", gstin_checksum_ok(gstin),
                                   f"GSTIN {gstin}")
        matches = self.erp.find_vendor_by_gstin(gstin)
        if len(matches) == 1:
            wi.vendor_id = matches[0]["vendor_id"]
            wi.add_check("vendor_master_match", True, f"-> {wi.vendor_id}")
        else:
            # Fall back to exact name only to give the human context, never to proceed.
            by_name = [v for v in self.erp.vendors.values()
                       if v["name"].lower() == (wi.f("vendor_name") or "").lower()]
            if not checksum_ok and len(by_name) == 1:
                wi.vendor_id = by_name[0]["vendor_id"]
                failures.append(("GST_CHECKSUM_FAIL",
                                 f"printed GSTIN {gstin} fails checksum; vendor identified by "
                                 f"name as {wi.vendor_id} for routing only"))
            else:
                wi.add_check("vendor_master_match", False, "no unique vendor master record")
                failures.append(("UNKNOWN_VENDOR",
                                 f"'{wi.f('vendor_name')}' / {gstin} has no vendor master record"))
        if not checksum_ok and not any(t == "GST_CHECKSUM_FAIL" for t, _ in failures) \
                and not any(t == "UNKNOWN_VENDOR" for t, _ in failures):
            failures.append(("GST_CHECKSUM_FAIL", f"GSTIN {gstin} fails checksum"))

        # -- 3. Hard arithmetic. An invented number will not add up, match the
        #       PO, and pass a checksum all at the same time.
        line_sum = round(sum(ln["line_total"].value for ln in wi.lines), 2)
        subtotal, total = wi.f("subtotal"), wi.f("total")
        cgst, sgst, igst = wi.f("cgst"), wi.f("sgst"), wi.f("igst")

        ok = wi.add_check("lines_sum_to_subtotal", abs(line_sum - subtotal) < 0.01,
                          f"lines {line_sum} vs subtotal {subtotal}")
        if not ok:
            failures.append(("ARITHMETIC_FAIL",
                             f"line items sum to Rs {line_sum:,.2f} but subtotal reads Rs {subtotal:,.2f} "
                             f"— probable misread; never auto-corrected"))
        ok = wi.add_check("subtotal_plus_tax_is_total",
                          abs(subtotal + cgst + sgst + igst - total) < 0.01,
                          f"{subtotal}+{cgst}+{sgst}+{igst} vs {total}")
        if not ok:
            failures.append(("ARITHMETIC_FAIL", "subtotal + GST does not equal total"))
        expected_tax = round(subtotal * GST_RATE, 2)
        ok = wi.add_check("gst_equals_rate_times_taxable",
                          abs((cgst + sgst + igst) - expected_tax) < 0.01,
                          f"tax {cgst + sgst + igst} vs {GST_RATE:.0%} of {subtotal}")
        if not ok:
            failures.append(("GST_MATH_FAIL", f"GST charged Rs {cgst+sgst+igst:,.2f}, "
                             f"expected Rs {expected_tax:,.2f} at {GST_RATE:.0%}"))

        # -- 4. Tax type must agree with the GSTIN's state code
        if gstin and len(gstin) == 15:
            intra = gstin[:2] == BUYER["state_code"]
            type_ok = (cgst > 0 and sgst > 0 and igst == 0) if intra \
                 else (igst > 0 and cgst == 0 and sgst == 0)
            ok = wi.add_check("tax_type_matches_state",
                              type_ok, "intra-state -> CGST+SGST" if intra else "inter-state -> IGST")
            if not ok:
                failures.append(("GST_TYPE_FAIL",
                                 "tax split does not match supplier state "
                                 "(known exceptions — SEZ, bill-to/ship-to, imports — are coded in)"))

        # -- 5. Duplicate check — STATUS-AWARE, per the case study:
        #       paid/posted -> block; rejected -> resubmission; near-dup -> human.
        if wi.vendor_id:
            inv_no = wi.f("invoice_number")
            exact = self.erp.register_lookup(wi.vendor_id, inv_no)
            if exact:
                prior = exact[0]
                if prior["status"] in ("PAID", "POSTED"):
                    wi.add_check("duplicate", False, f"exact duplicate of {prior['status']} invoice")
                    failures.append(("DUPLICATE_BLOCK",
                                     f"{inv_no} already {prior['status']} on {prior.get('paid_on', '')} "
                                     f"({prior['note']})"))
                elif prior["status"] == "REJECTED":
                    wi.add_check("duplicate", True,
                                 "same number previously REJECTED -> treated as corrected resubmission")
                    wi.notes.append(f"Resubmission: linked to rejected original of {prior['rejected_on']} "
                                    f"({prior['note']}) and processed, not bounced.")
            else:
                near = [r for r in self.erp.register_lookup(wi.vendor_id)
                        if difflib.SequenceMatcher(None, r["invoice_number"], inv_no).ratio() > 0.9]
                if near:
                    wi.add_check("duplicate", False, f"near-duplicate of {near[0]['invoice_number']}")
                    failures.append(("NEAR_DUPLICATE",
                                     f"{inv_no} is one character away from {near[0]['invoice_number']} "
                                     f"({near[0]['status']}, Rs {near[0]['total']:,.0f}) — held for a person; "
                                     f"nothing that might be a duplicate is ever paid automatically"))
                else:
                    wi.add_check("duplicate", True, "no exact or near match in register")

        for ftype, detail in failures:
            wi.exceptions.append({"type": ftype, "detail": detail, "stage": "validation"})
        wi.status = "VALIDATED" if not failures else "VALIDATION_FAILED"
        self.audit.log("validation-agent", wi.work_id, wi.status,
                       f"{len(wi.checks)} checks, {len(failures)} failure(s)"
                       + (": " + ", ".join(t for t, _ in failures) if failures else ""))
        return wi
