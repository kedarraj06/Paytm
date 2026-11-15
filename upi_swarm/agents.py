"""The six specialised agents + request-info / human-review / monitor nodes. Each returns a partial state update and a trace entry."""
import re, math, datetime as dt
import store
from store import now, uid
from institutions import INST, elapsed_days, parse_ts
from rules import RULES, CHANNEL_ENDPOINT, CLOSE_CATEGORIES, COMPLAINT_RESPONSE_DAYS, COMPENSATION_PER_DAY
import policy, llm

L_AI, L_EVID, L_SIM, L_OK, L_HUMAN = "AI recommendation", "Verified evidence", "Simulated API response", "Approved action", "Pending human approval"


def _log(state, node, label, summary, output, action):
    ts = now()
    store.put("upi_audit_logs", {"log_id": uid("L"), "case_id": state.get("case_id"), "agent_name": node, "action": action,
                                 "input_summary": f"case={state.get('case_id')} mode={state.get('mode', 'new')}",
                                 "output_summary": summary, "label": label, "ts": ts})
    return {"trace": [{"node": node, "label": label, "summary": summary, "output": output, "ts": ts}]}


def _txn(state):
    return store.get("upi_transactions", state["transaction_id"])


def _elapsed(state):
    t = _txn(state)
    return elapsed_days(t, state.get("sim_days", 0)) if t else 0


# ------------------------------------------------------------------ 1. Router
def router_agent(state):
    text = (state.get("description") or "").strip()
    txn_id = state.get("transaction_id")
    m = re.search(r"TXN[-_ ]?(\d+)", text, re.I)
    if not txn_id and m:
        txn_id = f"TXN-{m.group(1)}"
    am = re.search(r"(?:₹|rs\.?|inr)\s*([\d,]+)", text, re.I) or re.search(r"([\d,]+)\s*(?:rs|rupees)", text, re.I)
    amount = float(am.group(1).replace(",", "")) if am else None
    if not txn_id and state.get("user_id"):  # try to resolve by user + amount
        cand = [t for t in store.find("upi_transactions", order="ts DESC", sender_user_id=state["user_id"]) if amount is None or t["amount"] == amount]
        if amount is not None and cand:
            txn_id = cand[0]["transaction_id"]
    txn = store.get("upi_transactions", txn_id) if txn_id else None
    low = text.lower()
    claims = {
        "customer_debit_claim": bool(re.search(r"debit|deduct|cut|charged|paid|sent|kat", low)) or None,
        "merchant_credit_claim": False if re.search(r"not receiv|didn.t receiv|nahi|not credited|hasn.t received|not got|not reflect", low) else None,
        "app_shows_failed_claim": "fail" in low,
    }
    case_id = state.get("case_id") or f"PTM-{10021 + len(store.all_('upi_disputes'))}"
    out = {"case_id": case_id, "transaction_id": txn["transaction_id"] if txn else None,
           "amount": (txn or {}).get("amount", amount), "user_id": state.get("user_id") or (txn or {}).get("sender_user_id"),
           "claims": claims, "note": "Claims are the customer's statements - NOT verified facts."}
    missing = []
    if not txn:
        missing.append("TRANSACTION_ID_OR_UPI_REF")
    if txn and state.get("user_id") and txn["sender_user_id"] != state["user_id"]:
        missing.append("TRANSACTION_DOES_NOT_BELONG_TO_USER")
    if not text and not txn:
        missing.append("COMPLAINT_DESCRIPTION")
    if missing:
        out.update(issue_type="UNKNOWN", status="AWAITING_CUSTOMER_INFO", missing_information=missing)
        store.put("upi_disputes", {"case_id": case_id, "transaction_id": txn_id, "user_id": out["user_id"], "issue_category": "UNKNOWN",
                                   "status": "AWAITING_CUSTOMER_INFO", "created_at": now(), "updated_at": now(),
                                   "state": {"sim_days": 0}})
        upd = {"case_id": case_id, "router": out, "needs_info": True, "missing_information": missing, "status": "AWAITING_CUSTOMER_INFO"}
        return {**upd, **_log({**state, "case_id": case_id}, "router", L_AI, f"Missing: {', '.join(missing)}. Requesting more information.", out, "ROUTE_COMPLAINT")}
    if txn["kind"] == "MERCHANT":
        issue = "DEBITED_APP_SHOWS_FAILED" if claims["app_shows_failed_claim"] and claims["merchant_credit_claim"] is None else "MERCHANT_CREDIT_NOT_CONFIRMED"
    else:
        issue = "DEBITED_APP_SHOWS_FAILED" if claims["app_shows_failed_claim"] and claims["merchant_credit_claim"] is None else "BENEFICIARY_NOT_CREDITED"
    out.update(issue_type=issue, status="INVESTIGATION_PENDING", upi_ref=txn["upi_ref"], missing_information=[])
    store.put("upi_disputes", {"case_id": case_id, "transaction_id": txn["transaction_id"], "user_id": out["user_id"], "issue_category": issue,
                               "status": "INVESTIGATION_PENDING", "created_at": now(), "updated_at": now(), "state": {"sim_days": 0}})
    return {"case_id": case_id, "transaction_id": txn["transaction_id"], "user_id": out["user_id"], "router": out, "needs_info": False,
            "sim_days": 0, "status": "INVESTIGATION_PENDING",
            **_log({"case_id": case_id}, "router", L_AI, f"Case {case_id} created: {issue} for {txn['transaction_id']} (Rs.{txn['amount']:.0f}). Customer claims recorded, not verified.", out, "ROUTE_COMPLAINT")}


def request_info_node(state):
    msg = "Please share the transaction ID / UPI reference and the amount so we can investigate."
    store.put("upi_notifications", {"notification_id": uid("N"), "case_id": state["case_id"], "recipient": "CUSTOMER", "message": msg,
                                    "delivery_status": "SIMULATED_SENT", "ts": now()})
    return {"status": "AWAITING_CUSTOMER_INFO", **_log(state, "request_info", L_AI, "Asked customer for missing information: " + ", ".join(state.get("missing_information", [])), {"message": msg}, "REQUEST_INFO")}


# ------------------------------------------------------------------ 2. Investigator
ORDER = ["INITIATED", "CUSTOMER_DEBIT_CONFIRMED", "BENEFICIARY_CREDIT_CONFIRMED", "MERCHANT_CONFIRMED", "REVERSAL_CONFIRMED"]


def investigator_agent(state):
    tid, el = state["transaction_id"], _elapsed(state)
    paytm = INST.get_paytm_transaction(tid, el)
    debit = INST.check_customer_bank_debit(tid, el)
    credit = INST.check_beneficiary_bank_credit(tid, el)
    psp = INST.check_psp_transaction_status(tid, el)
    npci = INST.check_npci_transaction_status(tid, el)
    merch = INST.check_merchant_confirmation(tid, el)
    rev = INST.check_reversal_status(tid, el)
    t = paytm["record"]
    is_merchant = t["kind"] == "MERCHANT"
    checks = [("PAYTM", "PAYTM_TRANSACTION_RECORD", paytm), ("CUSTOMER_BANK", "CUSTOMER_DEBIT", debit), ("BENEFICIARY_BANK", "BENEFICIARY_CREDIT", credit),
              ("PSP_BANK", "PSP_PROCESSING_STATUS", psp), ("NPCI", "NPCI_NETWORK_STATUS", npci)]
    if is_merchant:
        checks.append(("MERCHANT_ACQUIRER", "MERCHANT_CONFIRMATION", merch))
    checks.append(("REVERSAL_LEDGER", "REVERSAL_STATUS", rev))
    for src, typ, r in checks:
        store.put("upi_evidence", {"evidence_id": f"{state['case_id']}-{typ}", "case_id": state["case_id"], "source_institution": src, "evidence_type": typ,
                                   "evidence_status": r["status"], "ts": now(), "response_payload": {k: v for k, v in r.items() if k != "record"}})
    conflicts = []
    if paytm["app_status"] == "SUCCESS" and debit["status"] == "NOT_CONFIRMED":
        conflicts.append("Paytm shows SUCCESS but customer-bank debit is not confirmed")
    if credit["status"] == "CONFIRMED" and npci["raw"] == "FAILED":
        conflicts.append("Beneficiary credit confirmed but NPCI reports FAILED")
    if rev["status"] == "CONFIRMED" and credit["status"] == "CONFIRMED":
        conflicts.append("Both reversal and beneficiary credit confirmed (possible double settlement)")
    unknown = [n for n, r in [("debit", debit), ("credit", credit), ("psp", psp), ("npci", npci)] + ([("merchant", merch)] if is_merchant else []) if r["status"] == "UNKNOWN"]
    if conflicts or debit["status"] == "UNKNOWN":
        conf = "LOW"
    else:
        conf = "HIGH" if len(unknown) <= 1 else "MEDIUM"
    missing = []
    if credit["status"] != "CONFIRMED":
        missing.append("BENEFICIARY_BANK_CREDIT_CONFIRMATION")
    if is_merchant and merch["status"] != "CONFIRMED":
        missing.append("MERCHANT_ACQUIRER_CONFIRMATION")
    last = "INITIATED"
    if rev["status"] == "CONFIRMED": last = "REVERSAL_CONFIRMED"
    elif is_merchant and merch["status"] == "CONFIRMED": last = "MERCHANT_CONFIRMED"
    elif credit["status"] == "CONFIRMED": last = "BENEFICIARY_CREDIT_CONFIRMED"
    elif debit["status"] == "CONFIRMED": last = "CUSTOMER_DEBIT_CONFIRMED"
    base = parse_ts(t["ts"])
    tl = [{"t": base.isoformat(timespec="seconds"), "source": "PAYTM", "event": "Payment initiated", "status": "CONFIRMED"}]
    for i, (src, typ, r) in enumerate(checks[1:], start=1):
        tl.append({"t": (base + dt.timedelta(seconds=2 * i)).isoformat(timespec="seconds"), "source": src, "event": typ.replace("_", " ").title(), "status": r["status"]})
    tl.append({"t": paytm_shown_ts(base), "source": "PAYTM", "event": f"App displays {paytm['app_status']}", "status": "CONFIRMED"})
    inv = {"transaction_id": tid, "amount": t["amount"], "kind": t["kind"], "paytm_app_status": paytm["app_status"], "customer_debit": debit["status"],
           "beneficiary_credit": credit["status"], "merchant_confirmation": merch["status"] if is_merchant else "NOT_APPLICABLE",
           "psp_status": psp["status"], "npci_status": npci["status"], "npci_raw": npci["raw"], "reversal_status": rev["status"],
           "last_confirmed_state": last, "evidence_confidence": conf, "missing_evidence": missing, "conflicts": conflicts,
           "elapsed_days": round(el, 2), "timeline": tl}
    fb = (f"Debit {debit['status']}, beneficiary credit {credit['status']}"
          + (f", merchant {merch['status']}" if is_merchant else "") + f", reversal {rev['status']}. Last confirmed state: {last}. Confidence {conf}.")
    summary, mode = llm.polish("In 2 factual sentences, summarise this UPI investigation for a case file. Do not speculate. Data: " + str(
        {k: v for k, v in inv.items() if k != "timeline"}), fb)
    inv["summary"] = summary
    return {"investigation": inv, **_log(state, "investigator", L_EVID, fb + (f" ⚠ {len(conflicts)} conflict(s)" if conflicts else ""), {**{k: v for k, v in inv.items() if k != "timeline"}, "summary_source": mode}, "INVESTIGATE")}


def paytm_shown_ts(base):
    return (base + dt.timedelta(seconds=20)).isoformat(timespec="seconds")


# ------------------------------------------------------------------ 3. Resolution & routing
def resolution_agent(state):
    inv = state["investigation"]
    d, c, m, r = inv["customer_debit"], inv["beneficiary_credit"], inv["merchant_confirmation"], inv["reversal_status"]
    is_merchant = inv["kind"] == "MERCHANT"
    if inv["conflicts"] or d == "UNKNOWN":
        cat, reason = "CONFLICTING_EVIDENCE", "Records from institutions conflict or the debit is unverifiable: " + "; ".join(inv["conflicts"] or ["debit status unknown"])
    elif r == "CONFIRMED":
        cat, reason = "REVERSAL_CONFIRMED", "Reversal is confirmed by the reversal ledger."
    elif d == "NOT_CONFIRMED":
        cat, reason = "NO_DEBIT_FOUND", "Customer-bank debit is not confirmed; no refund can be initiated without a debit."
    elif d == "CONFIRMED" and c == "CONFIRMED" and inv["paytm_app_status"] == "SUCCESS":
        cat, reason = "STATUS_RECONCILED", "Paytm status now matches the confirmed credit."
    elif d == "CONFIRMED" and c == "CONFIRMED" and inv["paytm_app_status"] == "FAILED":
        cat, reason = "STATUS_MISMATCH", "Beneficiary/merchant credit is confirmed but Paytm displays FAILED - status reconciliation needed, NOT a refund."
    elif d == "CONFIRMED" and is_merchant and m in ("PENDING", "NOT_CONFIRMED", "UNKNOWN"):
        cat, reason = "MERCHANT_CREDIT_NOT_CONFIRMED", "Customer debit is confirmed but merchant credit is not confirmed."
    elif d == "CONFIRMED":
        cat, reason = "BENEFICIARY_NOT_CREDITED", "Customer debit is confirmed but beneficiary credit is not confirmed."
    else:
        cat, reason = "CONFLICTING_EVIDENCE", "Evidence insufficient to classify."
    rule = RULES[cat]
    out = {"issue_category": cat, "responsible_next_party": rule["party"], "recommended_channel": rule["channel"],
           "recommended_action": rule["action"], "reason": reason, "applicable_sla": rule["sla_label"], "sla_days": rule["sla_days"],
           "sla_source": rule["sla_source"], "requires_human_review": rule["requires_human"]}
    origin = state.get("sla_origin")
    if rule["sla_days"]:
        origin = {"days": rule["sla_days"], "label": rule["sla_label"], "source": rule["sla_source"]}
    elif cat in CLOSE_CATEGORIES and origin:  # keep the TAT the case was tracked against, to judge on-time vs late closure
        out.update(sla_days=origin["days"], applicable_sla=origin["label"], sla_source=origin["source"])
    d_row = store.get("upi_disputes", state["case_id"])
    d_row.update(issue_category=cat, responsible_institution=rule["party"], applicable_sla=out["applicable_sla"], updated_at=now())
    store.put("upi_disputes", d_row)
    return {"resolution": out, "sla_origin": origin, **_log(state, "resolution", L_AI, f"{cat} -> {rule['party']} via {rule['channel'] or 'n/a'}; action {rule['action']}; SLA {rule['sla_label']}", out, "ROUTE_RESOLUTION")}


# ------------------------------------------------------------------ Policy engine node
def policy_node(state):
    v = policy.validate_action(state)
    label = {"APPROVED": L_OK, "HUMAN_REVIEW": L_HUMAN}.get(v["decision"], L_EVID)
    return {"policy": v, **_log(state, "policy", label, f"{v['decision']}: {v['reason']}", v, "VALIDATE_ACTION")}


def human_review_node(state):
    store.put("upi_notifications", {"notification_id": uid("N"), "case_id": state["case_id"], "recipient": "HUMAN_REVIEW_DESK",
                                    "message": f"Case {state['case_id']} needs approval: {state['policy']['reason']}", "delivery_status": "SIMULATED_SENT", "ts": now()})
    return {"human_review": True, "status": "PENDING_HUMAN_REVIEW",
            **_log(state, "human_review", L_HUMAN, "Escalated to human reviewer; no external action taken until approved.", {"checks": state["policy"]["checks"]}, "HUMAN_REVIEW")}


# ------------------------------------------------------------------ 4. Complaint management
def complaint_agent(state):
    inv, res, pol = state["investigation"], state["resolution"], state["policy"]
    t = _txn(state)
    ch = pol["channel"]
    existing = [c for c in store.find("upi_complaints", case_id=state["case_id"]) if c["dedupe_key"] == pol["dedupe_key"]]
    el = _elapsed(state)
    if pol["decision"] == "ALREADY_SUBMITTED" and existing:
        c = existing[0]
        st = INST.complaint_status(c["external_reference"], inv["transaction_id"], el)
        c["response"] = {**st, "checked_at": now()}
        c["submission_status"] = st["status"]
        followup = None
        sla = _sla_calc(state, c)
        if sla["status"] == "OVERDUE" and st["status"] != "RESOLVED":
            followup = f"Follow-up on {c['external_reference']}: TAT {res['applicable_sla']} exceeded; please share status and reversal ETA."
            c["response"]["followup"] = followup
        store.put("upi_complaints", c)
        return {"complaint": c, "followup": followup, **_log(state, "complaint_mgmt", L_SIM,
                f"Existing complaint {c['external_reference']} (no duplicate filed). Institution says: {st['status']}." + (" Follow-up generated." if followup else ""), {**c, "followup": followup}, "TRACK_COMPLAINT")}
    payload = {"subject": f"UPI {'Merchant Payment' if inv['kind'] == 'MERCHANT' else 'Fund Transfer'} Dispute - Rs.{t['amount']:.0f}",
               "transaction_id": t["transaction_id"], "upi_ref": t["upi_ref"], "amount": t["amount"], "timestamp": t["ts"],
               "issue": {"RAISE_RECONCILIATION_REQUEST": "Transaction status mismatch between Paytm and institution records."}.get(
                   res["recommended_action"], "Customer account debited, but credit/merchant confirmation is unavailable."),
               "evidence": {"customer_debit": inv["customer_debit"], "beneficiary_credit": inv["beneficiary_credit"],
                            "merchant_credit": inv["merchant_confirmation"], "reversal": inv["reversal_status"], "paytm_status": inv["paytm_app_status"]},
               "requested_action": "Verify the transaction status and provide the latest processing/reversal information.",
               "target_endpoint": CHANNEL_ENDPOINT[ch]}
    r = INST.submit_complaint(ch, payload)
    row = {"complaint_id": uid("C"), "case_id": state["case_id"], "institution": res["responsible_next_party"], "external_reference": r["reference_id"],
           "submission_status": "SUBMITTED", "response": {"status": "SUBMITTED", "message": "Simulated institution accepted the complaint.", "payload": payload},
           "submitted_at": now(), "dedupe_key": pol["dedupe_key"]}
    store.put("upi_complaints", row)
    return {"complaint": row, **_log(state, "complaint_mgmt", L_SIM, f"Complaint {r['reference_id']} submitted to {res['responsible_next_party']} via {CHANNEL_ENDPOINT[ch]}.", {**row, "payload": payload}, "SUBMIT_COMPLAINT")}


# ------------------------------------------------------------------ 5. SLA monitoring
def _sla_calc(state, complaint=None):
    res, inv, t = state["resolution"], state["investigation"], _txn(state)
    days = res.get("sla_days")
    el = _elapsed(state)
    if days is None:
        return {"status": "NOT_APPLICABLE", "label": res["applicable_sla"], "elapsed_days": round(el, 2), "overdue_days": 0}
    deadline = parse_ts(t["ts"]) + dt.timedelta(days=days)
    rev_day = None
    g = store.get("upi_ground_truth", t["transaction_id"]) or {}
    if inv["reversal_status"] == "CONFIRMED":
        rev_day = g.get("reversal_day")
    resolved = res["issue_category"] in CLOSE_CATEGORIES
    over = max(0.0, (rev_day if (resolved and rev_day is not None) else el) - days)
    if resolved:
        status = "RESOLVED_LATE" if over > 0 else "RESOLVED_ON_TIME"
    else:
        status = "OVERDUE" if el > days else "ON_TRACK"
    comp_days = math.ceil(over) if over > 0 else 0
    comp_status = "NOT_APPLICABLE" if res["issue_category"] in ("STATUS_MISMATCH", "STATUS_RECONCILED", "NO_DEBIT_FOUND") else (
        "POTENTIAL" if (status == "OVERDUE" and comp_days) else ("PAYABLE_REVIEW" if status == "RESOLVED_LATE" else "NONE"))
    return {"status": status, "label": res["applicable_sla"], "sla_days": days, "deadline": deadline.isoformat(timespec="seconds"),
            "elapsed_days": round(el, 2), "remaining_days": round(days - el, 2), "overdue_days": round(over, 2),
            "compensation_status": comp_status, "potential_compensation_inr": comp_days * COMPENSATION_PER_DAY if comp_status != "NOT_APPLICABLE" else 0,
            "source": res["sla_source"], "complaint_response_deadline_days": COMPLAINT_RESPONSE_DAYS}


def sla_agent(state):
    sla = _sla_calc(state)
    c = state.get("complaint")
    age = 0.0
    if c:
        age = (dt.datetime.now(dt.timezone.utc) - parse_ts(c["submitted_at"])).total_seconds() / 86400 + state.get("sim_days", 0)
    sla["complaint_age_days"] = round(age, 2)
    esc = policy.validate_escalation(sla, age, state.get("human_approved", False))
    sla["escalation"] = esc
    done = list(state.get("reminders_sent", []))
    action = f"Day {int(sla['elapsed_days'])}: continue monitoring; reversal {state['investigation']['reversal_status']}."
    if esc["permitted"] and esc["level"] and esc["level"] not in done and c:
        done.append(esc["level"])
        action = f"{esc['level']} triggered: {esc['reason']}"
        store.put("upi_notifications", {"notification_id": uid("N"), "case_id": state["case_id"], "recipient": "INSTITUTION",
                                        "message": f"[{esc['level']}] {action} (ref {c['external_reference']})", "delivery_status": "SIMULATED_SENT", "ts": now()})
    elif esc["level"] == "RBI_OMBUDSMAN" and not esc["permitted"]:
        action = esc["reason"]
    sla["next_action"] = action
    d = store.get("upi_disputes", state["case_id"])
    d["deadline"] = sla.get("deadline")
    store.put("upi_disputes", d)
    lbl = L_OK if esc["permitted"] and esc["level"] else L_EVID
    return {"sla": sla, "reminders_sent": done, "escalation_level": esc.get("level"),
            **_log(state, "sla", lbl, f"{sla['label']} {sla['status']} | elapsed {sla['elapsed_days']}d | {action}", sla, "MONITOR_SLA")}


# ------------------------------------------------------------------ 6. Communication
def _send(case_id, recipient, msg):
    last = [n for n in store.find("upi_notifications", case_id=case_id, recipient=recipient)]
    if last and sorted(last, key=lambda n: n["ts"])[-1]["message"] == msg:
        return False
    store.put("upi_notifications", {"notification_id": uid("N"), "case_id": case_id, "recipient": recipient, "message": msg,
                                    "delivery_status": "SIMULATED_SENT", "ts": now()})
    if recipient == "CUSTOMER":
        try:
            import whatsapp; whatsapp.mirror_customer_message(case_id, msg)
        except Exception as e:
            print("[wa mirror]", e)
    return True


def comms_agent(state):
    inv, res, t = state["investigation"], state["resolution"], _txn(state)
    cat, cid = res["issue_category"], state["case_id"]
    c = state.get("complaint") or {}
    amt = f"Rs.{t['amount']:.0f}"
    ref = f" Complaint reference: {c['external_reference']}." if c else ""
    dl = f" Applicable timeline: {res['applicable_sla']}." if res.get("sla_days") else ""
    if state.get("status") == "PENDING_HUMAN_REVIEW" or state.get("human_review"):
        cust = f"Your {amt} transaction (case {cid}) needs a manual review by our team because institution records do not fully agree. We will update you; no action is needed from you."
    elif cat == "REVERSAL_CONFIRMED":
        cust = f"Good news: the reversal for your {amt} transaction is confirmed by the institution's records. Case {cid} is now closed."
    elif cat == "STATUS_RECONCILED":
        cust = f"Your {amt} payment has been confirmed as credited and the status in the app has been corrected. Case {cid} is closed. No refund is needed."
    elif cat == "NO_DEBIT_FOUND":
        cust = f"We checked your {amt} transaction (case {cid}) and could not confirm a debit from your account, so no refund applies. If your bank statement shows a debit, share it and we will re-investigate."
    elif cat == "STATUS_MISMATCH":
        cust = (f"Your {amt} payment shows as failed in the app, but the receiving side confirms the credit. A status reconciliation request has been raised; "
                f"no refund or second payment will be made because the payment was received.{ref}{dl} Case {cid}.")
    else:
        cust = (f"Your {amt} transaction has been investigated. Your bank debit is confirmed, but the {'merchant' if inv['kind'] == 'MERCHANT' else 'beneficiary'} credit has not yet been verified. "
                f"A dispute has been submitted through the relevant bank/PSP channel. We are tracking the resolution timeline; this is not a guarantee of outcome.{ref}{dl} Case reference: {cid}.")
    cust_final, src = llm.polish("Rewrite this customer notification in clear friendly plain English, same facts only, max 70 words, do not add promises: " + cust, cust,
                                 allow_refund_words=(cat == "REVERSAL_CONFIRMED"))
    sent = [("CUSTOMER", cust_final)]
    if t["kind"] == "MERCHANT":  # merchants never see customer identity, bank, or any risk labels
        mm = f"A payment of {amt} (UPI ref ****{t['upi_ref'][-4:]}) made to you is being verified by the payment network. No action is needed from you."
        if cat in ("STATUS_MISMATCH", "STATUS_RECONCILED"):
            mm = f"Payment {amt} (UPI ref ****{t['upi_ref'][-4:]}) is confirmed as credited to you."
        sent.append(("MERCHANT", mm))
    delivered = [r for r, m in sent if _send(cid, r, m)]
    return {"comms": {"customer": cust_final, "merchant": dict(sent).get("MERCHANT"), "delivered_to": delivered, "text_source": src},
            **_log(state, "comms", L_AI, f"Notified {', '.join(delivered) or 'nobody new (identical message already sent)'}", {"customer": cust_final, "merchant": dict(sent).get("MERCHANT"), "text_source": src}, "NOTIFY")}


# ------------------------------------------------------------------ Monitor / closure
def monitor_node(state):
    res = state.get("resolution", {})
    cat, pol = res.get("issue_category"), state.get("policy", {}).get("decision")
    if state.get("needs_info"): st = "AWAITING_CUSTOMER_INFO"
    elif cat in CLOSE_CATEGORIES: st = "RESOLVED"
    elif cat == "NO_DEBIT_FOUND": st = "CLOSED_NO_DEBIT"
    elif pol == "HUMAN_REVIEW" or state.get("human_review"): st = "PENDING_HUMAN_REVIEW"
    elif state.get("complaint"): st = "AWAITING_INSTITUTION_RESPONSE"
    else: st = "INVESTIGATION_PENDING"
    if st in ("RESOLVED", "CLOSED_NO_DEBIT"):
        for c in store.find("upi_complaints", case_id=state["case_id"]):
            if c["submission_status"] != "RESOLVED":
                c["submission_status"] = "RESOLVED"
                c["response"] = {**(c["response"] or {}), "status": "RESOLVED", "message": "Closed on authoritative evidence.", "checked_at": now()}
                store.put("upi_complaints", c)
    d = store.get("upi_disputes", state["case_id"])
    st_blob = {k: v for k, v in state.items() if k not in ("trace",)}
    st_blob["last_checked_day"] = int(state.get("investigation", {}).get("elapsed_days", 0))
    st_blob["status"] = st
    d.update(status=st, updated_at=now(), state=st_blob)
    store.put("upi_disputes", d)
    try:
        import graph_sync; graph_sync.sync_case_async(state["case_id"])
    except Exception:
        pass
    return {"status": st, **_log(state, "monitor", L_OK if st in ("RESOLVED", "CLOSED_NO_DEBIT") else L_EVID,
                                 f"Case status -> {st}" + (" (closed on evidence)" if st in ("RESOLVED", "CLOSED_NO_DEBIT") else " (monitoring continues)"), {"status": st}, "CASE_STATE")}
