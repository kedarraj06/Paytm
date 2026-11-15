"""End-to-end checks: 3 mandatory scenarios + edge cases, duplicate prevention, SLA, escalation, closure."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["USE_LLM"] = "0"
import seed, store, graph

seed.seed_local()
ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, extra)


def nodes(s): return [t["node"] for t in s["trace"]]


# Scenario 1
s = graph.run_new("I sent Rs.500 to a friend. My bank account was debited but he did not receive the money.", "USR_TEJAS", "TXN-5001")
c1 = s["case_id"]
check("S1 category", s["resolution"]["issue_category"] == "BENEFICIARY_NOT_CREDITED", s["resolution"]["issue_category"])
check("S1 T+1 + bank channel", s["resolution"]["applicable_sla"] == "T+1" and s["policy"]["channel"] == "bank")
check("S1 complaint submitted", s["complaint"]["external_reference"].startswith("BNK-"))
check("S1 flow", nodes(s) == ["router", "investigator", "resolution", "policy", "complaint_mgmt", "sla", "comms", "monitor"], nodes(s))
check("S1 last state", s["investigation"]["last_confirmed_state"] == "CUSTOMER_DEBIT_CONFIRMED")
# duplicate prevention
s = graph.run_followup(c1, 0)
check("S1 no duplicate complaint", len(store.find("upi_complaints", case_id=c1)) == 1)
s = graph.run_followup(c1, 2)  # day 2 -> past T+1, not yet reversed
check("S1 overdue + reminder", s["sla"]["status"] == "OVERDUE" and s["escalation_level"] == "REMINDER", str(s["sla"]["status"]))
check("S1 potential compensation", s["sla"]["potential_compensation_inr"] > 0)
check("S1 not escalated to RBI", s["sla"]["escalation"]["level"] != "RBI_OMBUDSMAN")
s = graph.run_followup(c1, 1)  # day 3 -> reversal confirmed
check("S1 closed on reversal", s["status"] == "RESOLVED" and s["investigation"]["reversal_status"] == "CONFIRMED", s["status"])
check("S1 late-resolution flagged", s["sla"]["status"] == "RESOLVED_LATE")

# Scenario 2
s = graph.run_new("I paid Rs.1000 to a merchant, debited but merchant did not receive", "USR_KEDAR", "TXN-5002")
c2 = s["case_id"]
check("S2 category/SLA", s["resolution"]["issue_category"] == "MERCHANT_CREDIT_NOT_CONFIRMED" and s["resolution"]["applicable_sla"] == "T+5")
check("S2 psp channel", s["complaint"]["external_reference"].startswith("PSP-"))
check("S2 merchant msg has no customer data", s["comms"]["merchant"] and "Kedar" not in s["comms"]["merchant"] and "ICICI" not in s["comms"]["merchant"])
check("S2 confidence HIGH w/ missing credit", s["investigation"]["evidence_confidence"] == "HIGH" and "BENEFICIARY_BANK_CREDIT_CONFIRMATION" in s["investigation"]["missing_evidence"])
s = graph.run_followup(c2, 2)
check("S2 on track Day2 monitoring", s["sla"]["status"] == "ON_TRACK" and s["escalation_level"] is None)
s = graph.run_followup(c2, 2)
check("S2 closed (reversal day 4, within T+5)", s["status"] == "RESOLVED" and s["sla"]["status"] == "RESOLVED_ON_TIME", s["status"])

# Scenario 3
s = graph.run_new("Paytm shows failed but Rs.750 was deducted", "USR_SHIVA", "TXN-5003")
c3 = s["case_id"]
check("S3 mismatch", s["resolution"]["issue_category"] == "STATUS_MISMATCH")
check("S3 reconciliation not refund", s["resolution"]["recommended_action"] == "RAISE_RECONCILIATION_REQUEST")
check("S3 customer told merchant credited", "no refund" in s["comms"]["customer"].lower())
s = graph.run_followup(c3, 1)
check("S3 reconciled + closed", s["status"] == "RESOLVED")

# Edge: not debited
s = graph.run_new("Payment failed but I think money was deducted", "USR_TEJAS", "TXN-5004")
check("S4 no debit -> no complaint/refund", s["resolution"]["issue_category"] == "NO_DEBIT_FOUND" and not s.get("complaint") and s["status"] == "CLOSED_NO_DEBIT", s["status"])
check("S4 claim vs fact", s["router"]["claims"]["customer_debit_claim"] and s["investigation"]["customer_debit"] == "NOT_CONFIRMED")

# Edge: conflicting evidence -> human review -> approve
s = graph.run_new("Sent Rs.1200, receiver says nothing arrived", "USR_KEDAR", "TXN-5005")
c5 = s["case_id"]
check("S5 conflict -> human review, no complaint", s["status"] == "PENDING_HUMAN_REVIEW" and not s.get("complaint"), s["status"])
s = graph.run_followup(c5, 0, True)
check("S5 approved -> complaint filed once", s["complaint"] and len(store.find("upi_complaints", case_id=c5)) == 1)

# Edge: missing info
s = graph.run_new("my money is stuck please help", "USR_SHIVA", None)
check("Missing info -> request_info", s["status"] == "AWAITING_CUSTOMER_INFO" and "request_info" in nodes(s))

# audit trail
check("Audit rows written", len(store.find("upi_audit_logs", case_id=c1)) >= 8)
print("\nALL PASS" if ok else "\nSOME FAILED")
sys.exit(0 if ok else 1)
