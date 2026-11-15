"""Deterministic Policy Engine (NOT an AI agent). Validates every action the agents recommend."""
import store
from rules import RULES, ALLOWED_ACTIONS, FORBIDDEN_ACTIONS, CLOSE_CATEGORIES, NODAL_ESCALATION_AFTER_OVERDUE_DAYS, COMPLAINT_RESPONSE_DAYS


def _chk(name, ok, detail):
    return {"check": name, "passed": bool(ok), "detail": detail}


def validate_action(state):
    inv, res = state["investigation"], state["resolution"]
    cat, action = res["issue_category"], res["recommended_action"]
    rule = RULES.get(cat, {})
    txn_id = inv["transaction_id"]
    dedupe = f"{txn_id}:{rule.get('channel')}:{cat}"
    dup = [c for c in store.find("upi_complaints", case_id=state["case_id"]) if c["dedupe_key"] == dedupe]
    conflicts = inv.get("conflicts", [])
    debit_ok = inv["customer_debit"] == "CONFIRMED"
    human_needed = rule.get("requires_human") or res.get("requires_human_review") or inv["evidence_confidence"] == "LOW" or bool(conflicts)
    checks = [
        _chk("evidence_sufficient", debit_ok or cat in CLOSE_CATEGORIES or cat == "NO_DEBIT_FOUND",
             f"customer_debit={inv['customer_debit']}, confidence={inv['evidence_confidence']}"),
        _chk("action_allowed", action in ALLOWED_ACTIONS and action not in FORBIDDEN_ACTIONS,
             f"{action} is {'on the allow-list' if action in ALLOWED_ACTIONS else 'NOT allowed (refund/transfer/block are never agent actions)'}"),
        _chk("institution_matches_rule", res["responsible_next_party"] == rule.get("party"),
             f"route {res['responsible_next_party']} vs rule {rule.get('party')}"),
        _chk("no_duplicate_payment", not (inv["beneficiary_credit"] == "CONFIRMED" and action not in ("RAISE_RECONCILIATION_REQUEST", "CLOSE_CASE", "VERIFY_STATUS_NO_REFUND")),
             "beneficiary already credited -> no second payment/refund allowed" if inv["beneficiary_credit"] == "CONFIRMED" else "no prior credit"),
        _chk("not_already_submitted", not dup, "complaint already exists for this case/channel" if dup else "no prior complaint for this channel"),
        _chk("human_approval", not human_needed or state.get("human_approved"),
             "human approval required (conflict/low confidence/rule)" if human_needed and not state.get("human_approved")
             else ("human approved" if human_needed else "not required")),
    ]
    failed = [c["check"] for c in checks if not c["passed"]]
    if cat in CLOSE_CATEGORIES:
        decision, why = "NO_ACTION", "Case resolved on evidence; closing."
    elif action in FORBIDDEN_ACTIONS or "action_allowed" in failed:
        decision, why = "BLOCKED", "Action blocked by policy."
    elif "human_approval" in failed:
        decision, why = "HUMAN_REVIEW", "Held for human approval."
    elif "no_duplicate_payment" in failed or "evidence_sufficient" in failed or "institution_matches_rule" in failed:
        decision, why = "BLOCKED", "Policy validation failed: " + ", ".join(failed)
    elif not rule.get("channel"):
        decision, why = "NO_ACTION", "No institution complaint applicable for this category."
    elif dup:
        decision, why = "ALREADY_SUBMITTED", "Duplicate complaint prevented; tracking existing one."
    else:
        decision, why = "APPROVED", "All checks passed; complaint submission authorised."
    return {"decision": decision, "reason": why, "checks": checks, "dedupe_key": dedupe, "channel": rule.get("channel")}


def validate_escalation(sla, complaint_age_days, human_approved=False):
    """Escalation only follows the grievance hierarchy; an expired timer alone never means RBI."""
    overdue_days = sla.get("overdue_days", 0)
    if sla.get("status") != "OVERDUE":
        return {"permitted": False, "level": None, "reason": "Not overdue - continue monitoring."}
    if complaint_age_days >= COMPLAINT_RESPONSE_DAYS:
        return {"permitted": bool(human_approved), "level": "RBI_OMBUDSMAN",
                "reason": "30-day complaint response window exhausted; Ombudsman route eligible but needs human approval."}
    if overdue_days >= NODAL_ESCALATION_AFTER_OVERDUE_DAYS:
        return {"permitted": True, "level": "NODAL_OFFICER", "reason": f"{overdue_days:.0f} days past TAT: escalate to institution nodal officer (L2)."}
    return {"permitted": True, "level": "REMINDER", "reason": "Past TAT: send follow-up reminder through the same channel (L1)."}
