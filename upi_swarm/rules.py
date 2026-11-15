"""Configurable dispute rules. Routing is driven by this table + evidence, never by LLM guesses."""
RULES = {
    "BENEFICIARY_NOT_CREDITED": dict(party="REMITTER_BANK_VIA_PSP", channel="bank", action="RAISE_TRANSACTION_STATUS_DISPUTE",
                                     sla_days=1, sla_label="T+1", sla_source="RBI TAT: debited, beneficiary not credited", requires_human=False),
    "MERCHANT_CREDIT_NOT_CONFIRMED": dict(party="RELEVANT_PSP_OR_BANK_CHANNEL", channel="psp", action="RAISE_TRANSACTION_STATUS_DISPUTE",
                                          sla_days=5, sla_label="T+5", sla_source="RBI TAT: debited, merchant confirmation not received", requires_human=False),
    "STATUS_MISMATCH": dict(party="PAYTM_STATUS_RECONCILIATION", channel="psp", action="RAISE_RECONCILIATION_REQUEST",
                            sla_days=1, sla_label="T+1 (ops)", sla_source="Operational reconciliation target (not a refund timeline)", requires_human=False),
    "CONFLICTING_EVIDENCE": dict(party="HUMAN_REVIEW_DESK", channel="psp", action="RAISE_RECONCILIATION_REQUEST",
                                 sla_days=5, sla_label="T+5 (ops)", sla_source="Operational target pending human review", requires_human=True),
    "NO_DEBIT_FOUND": dict(party="PAYTM_SUPPORT", channel=None, action="VERIFY_STATUS_NO_REFUND",
                           sla_days=None, sla_label="N/A", sla_source="No debit confirmed - nothing to reverse", requires_human=False),
    "REVERSAL_CONFIRMED": dict(party="NONE", channel=None, action="CLOSE_CASE", sla_days=None, sla_label="N/A", sla_source="Resolved", requires_human=False),
    "STATUS_RECONCILED": dict(party="NONE", channel=None, action="CLOSE_CASE", sla_days=None, sla_label="N/A", sla_source="Resolved", requires_human=False),
}
CHANNEL_ENDPOINT = {"bank": "POST /mock/bank/complaints", "psp": "POST /mock/psp/complaints", "npci": "POST /mock/npci/disputes"}
ALLOWED_ACTIONS = {"RAISE_TRANSACTION_STATUS_DISPUTE", "RAISE_RECONCILIATION_REQUEST", "VERIFY_STATUS_NO_REFUND", "CLOSE_CASE", "CONTINUE_MONITORING"}
FORBIDDEN_ACTIONS = {"INITIATE_REFUND", "TRANSFER_FUNDS", "BLOCK_ACCOUNT", "INITIATE_PAYMENT"}
CLOSE_CATEGORIES = {"REVERSAL_CONFIRMED", "STATUS_RECONCILED"}
COMPLAINT_RESPONSE_DAYS = 30          # tracked separately from the transaction reversal TAT
COMPENSATION_PER_DAY = 100            # RBI: Rs.100/day of delay beyond TAT (potential, only tracked)
NODAL_ESCALATION_AFTER_OVERDUE_DAYS = 3
