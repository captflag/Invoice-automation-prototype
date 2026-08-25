"""Central configuration — every threshold the case study mentions lives here,
not inside agent code, so tightening a gate is a config change, not a code change."""

BUYER = {
    "name": "Meridian Manufacturing Ltd",
    "gstin": "29AAACB1234F1Z5",
    "state_code": "29",  # Karnataka. Same-state vendors must bill CGST+SGST, others IGST.
}

GST_RATE = 0.18  # single rate for the demo; real systems read rate per HSN code

# Delegation of Authority (DoA) gates, in INR (invoice total incl. GST)
DOA = {
    "auto_post_limit": 100_000,      # <= this and fully clean: post + schedule, no human touch
    "manager_approval_limit": 500_000,  # <= this: posts, but payment needs one manager approval
    # above manager_approval_limit: always two approvers, however clean the match
}

MATCH_TOLERANCE_PCT = 0.01   # 1% price tolerance band; passes inside it are LOGGED, never silent
CONFIDENCE_FLOOR = 0.90      # any extracted field below this goes to the human verify queue
NEW_VENDOR_PROBATION = 3     # a new vendor's first N invoices are always human-reviewed
AUDIT_SAMPLE_RATE = 0.02     # share of touchless invoices audited daily, value-weighted

# What the agents' ERP service account is allowed to do. This mirrors the case
# study's core rule: the limits sit in the ERP's permission system, not in the
# AI's instructions. See erp_mock.MockERP for enforcement.
AGENT_ERP_ROLE = "invoice-clerk"
ROLE_PERMISSIONS = {
    "invoice-clerk": {"create_draft", "post_invoice", "read"},
    "ap-manager":    {"create_draft", "post_invoice", "read", "approve_payment"},
    "treasury":      {"read", "release_payment_run"},
    "vendor-admin":  {"read", "change_bank_details"},
}
