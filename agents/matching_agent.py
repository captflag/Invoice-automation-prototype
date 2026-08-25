"""Matching Agent — 2-way and 3-way matching. Plain code with clear rules, not
AI judgment. The one AI-shaped assist is pairing invoice lines to PO lines when
there is no item code to join on — and even then the numbers on the paired
lines are checked by code afterwards."""
import difflib
from config import MATCH_TOLERANCE_PCT


class MatchingAgent:
    def __init__(self, erp, audit):
        self.erp = erp
        self.audit = audit

    def _pair_line(self, inv_line, po_lines):
        """Join on item_code when present; otherwise fuzzy description pairing
        (the stand-in for the LLM line-pairing assist)."""
        code = inv_line["item_code"].value
        if code:
            for pl in po_lines:
                if pl["item_code"] == code:
                    return pl, "item_code"
        best, score = None, 0.0
        for pl in po_lines:
            r = difflib.SequenceMatcher(None, inv_line["description"].value.lower(),
                                        pl["description"].lower()).ratio()
            if r > score:
                best, score = pl, r
        return (best, f"description similarity {score:.2f}") if score > 0.6 else (None, "no pair")

    def run(self, wi):
        failures = []
        po_number = wi.f("po_number")

        if not po_number:
            wi.add_check("po_present", False, "no PO number on invoice")
            wi.exceptions.append({"type": "MISSING_PO", "stage": "matching",
                                  "detail": "invoice carries no PO number"})
            wi.status = "MATCH_FAILED"
            self.audit.log("matching-agent", wi.work_id, "MATCH_FAILED", "missing PO")
            return wi
        wi.add_check("po_present", True, po_number)

        po = self.erp.get_po(po_number)
        if not po or po["vendor_id"] != wi.vendor_id:
            failures.append(("PO_VENDOR_MISMATCH", f"{po_number} missing or belongs to another vendor"))
        else:
            wi.po_type = po["type"]
            wi.add_check("po_header_match",
                         po["currency"] == wi.f("currency") and po["payment_terms"] == wi.f("payment_terms"),
                         f"currency {wi.f('currency')}, terms {wi.f('payment_terms')}")

            grn_lines = {}
            if po["type"] == "goods":
                for g in self.erp.grns_for_po(po_number):
                    for l in g["lines"]:
                        grn_lines[l["item_code"]] = grn_lines.get(l["item_code"], 0) + l["qty_received"]

            for inv_line in wi.lines:
                po_line, how = self._pair_line(inv_line, po["lines"])
                desc = inv_line["description"].value
                if not po_line:
                    failures.append(("LINE_UNMATCHED", f"'{desc}' pairs with no PO line"))
                    continue
                wi.add_check(f"line_paired[{desc[:24]}]", True, f"via {how}")

                # Price check with tolerance band. Inside the band passes but is
                # LOGGED — a tolerance band is exactly where a supplier can hide
                # a slow price increase, so the log gets read monthly.
                inv_price, po_price = inv_line["unit_price"].value, po_line["unit_price"]
                diff_pct = abs(inv_price - po_price) / po_price if po_price else 1.0
                if diff_pct == 0:
                    wi.add_check(f"price[{desc[:24]}]", True, f"exact {po_price}")
                elif diff_pct <= MATCH_TOLERANCE_PCT:
                    wi.add_check(f"price[{desc[:24]}]", True,
                                 f"{inv_price} vs PO {po_price} (+{diff_pct:.2%}) — inside tolerance, logged")
                    wi.notes.append(f"Tolerance pass on '{desc}': billed {inv_price} vs PO {po_price} "
                                    f"(+{diff_pct:.2%}). Logged for the monthly price-creep review.")
                    self.audit.log("matching-agent", wi.work_id, "TOLERANCE_PASS_LOGGED",
                                   f"'{desc}' +{diff_pct:.2%}")
                else:
                    wi.add_check(f"price[{desc[:24]}]", False,
                                 f"{inv_price} vs PO {po_price} (+{diff_pct:.2%})")
                    failures.append(("PRICE_MISMATCH",
                                     f"'{desc}': billed Rs {inv_price} vs PO Rs {po_price} "
                                     f"(+{diff_pct:.2%}, tolerance {MATCH_TOLERANCE_PCT:.0%}) — the gap is "
                                     f"PRICE, not quantity, on this line"))

                # 3-way for goods: cannot bill more than received minus already invoiced
                if po["type"] == "goods":
                    code = po_line["item_code"]
                    received = grn_lines.get(code, 0)
                    already = self.erp.already_invoiced_qty(po_number, code)
                    billable = received - already
                    qty = inv_line["qty"].value
                    ok = wi.add_check(f"qty_vs_receipt[{desc[:24]}]", qty <= billable,
                                      f"billing {qty}, received {received}, already invoiced {already}")
                    if not ok:
                        failures.append(("QTY_OVER_RECEIPT",
                                         f"'{desc}': billing {qty} but only {billable} billable "
                                         f"(received {received}, already invoiced {already}) — the gap is "
                                         f"QUANTITY, not price, on this line"))

        for ftype, detail in failures:
            wi.exceptions.append({"type": ftype, "detail": detail, "stage": "matching"})
        wi.status = "MATCHED" if not failures else "MATCH_FAILED"
        match_kind = "3-way" if wi.po_type == "goods" else "2-way"
        self.audit.log("matching-agent", wi.work_id, wi.status,
                       f"{match_kind} vs {po_number}: {len(failures)} failure(s)")
        return wi
