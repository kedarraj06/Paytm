"""Seed fake data: SQLite (+ Supabase mirror when upi_* tables exist) and Neo4j.   python seed.py [--no-neo4j]"""
import sys, datetime as dt
import store

USERS = [
    ("USR_SHIVA", "Shiva Singh", "+91 98200 11001", "shiva.singh@example.com", "SBI", "shiva@paytm"),
    ("USR_TEJAS", "Tejas Satish Kadam", "+91 98200 11002", "tejas.kadam@example.com", "HDFC Bank", "tejas@paytm"),
    ("USR_KEDAR", "Kedarraj Bhimrao Birader", "+91 98200 11003", "kedarraj.birader@example.com", "ICICI Bank", "kedarraj@paytm"),
]
UB = {u[0]: (u[1], u[4]) for u in USERS}
# id, sender, beneficiary_name, beneficiary_bank, merchant, amount, hours_ago, app_status, kind, note,
#   debit, credit, merchant_conf, psp, npci, reversal, reversal_day, reconcile_day
TX = [
    ("TXN-5001", "USR_TEJAS", "Shiva Singh", "SBI", None, 500, 3, "PENDING", "P2P", "Scenario 1: failed fund transfer",
     "CONFIRMED", "NOT_CONFIRMED", None, "PENDING", "TIMEOUT", "NOT_CONFIRMED", 3, None),
    ("TXN-5002", "USR_KEDAR", "Chai Point Cafe", "Axis Bank", "MERCH_CHAI_POINT", 1000, 3, "PENDING", "MERCHANT", "Scenario 2: failed merchant payment",
     "CONFIRMED", "UNKNOWN", "PENDING", "PENDING", "PENDING", "NOT_CONFIRMED", 4, None),
    ("TXN-5003", "USR_SHIVA", "Book House", "Kotak Bank", "MERCH_BOOK_HOUSE", 750, 3, "FAILED", "MERCHANT", "Scenario 3: Paytm shows failed, merchant credited",
     "CONFIRMED", "CONFIRMED", "CONFIRMED", "CONFIRMED", "SUCCESS", "NOT_CONFIRMED", None, 1),
    ("TXN-5004", "USR_TEJAS", "Kedarraj Bhimrao Birader", "ICICI Bank", None, 300, 3, "FAILED", "P2P", "Edge: customer never debited",
     "NOT_CONFIRMED", "NOT_CONFIRMED", None, "NOT_CONFIRMED", "FAILED", "NOT_CONFIRMED", None, None),
    ("TXN-5005", "USR_KEDAR", "Shiva Singh", "SBI", None, 1200, 3, "PENDING", "P2P", "Edge: conflicting institution records",
     "CONFIRMED", "CONFIRMED", None, "CONFIRMED", "FAILED", "NOT_CONFIRMED", None, None),
    ("TXN-5006", "USR_SHIVA", "Tejas Satish Kadam", "HDFC Bank", None, 250, 30, "PENDING", "P2P", "Demo: late reversal",
     "CONFIRMED", "NOT_CONFIRMED", None, "PENDING", "TIMEOUT", "NOT_CONFIRMED", 2, None),
    ("TXN-5007", "USR_TEJAS", "Chai Point Cafe", "Axis Bank", "MERCH_CHAI_POINT", 2499, 6, "PENDING", "MERCHANT", "Demo: merchant payment (WhatsApp)",
     "CONFIRMED", "UNKNOWN", "PENDING", "PENDING", "PENDING", "NOT_CONFIRMED", 5, None),
    ("TXN-5008", "USR_KEDAR", "Book House", "Kotak Bank", "MERCH_BOOK_HOUSE", 90, 20, "FAILED", "MERCHANT", "Demo: status mismatch (WhatsApp)",
     "CONFIRMED", "CONFIRMED", "CONFIRMED", "CONFIRMED", "SUCCESS", "NOT_CONFIRMED", None, 3),
    ("TXN-5009", "USR_SHIVA", "Kedarraj Bhimrao Birader", "ICICI Bank", None, 5000, 50, "PENDING", "P2P", "Demo: long overdue (WhatsApp)",
     "CONFIRMED", "NOT_CONFIRMED", None, "PENDING", "TIMEOUT", "NOT_CONFIRMED", 12, None),
    ("TXN-4001", "USR_SHIVA", "Chai Point Cafe", "Axis Bank", "MERCH_CHAI_POINT", 60, 72, "SUCCESS", "MERCHANT", "History", "CONFIRMED", "CONFIRMED", "CONFIRMED", "CONFIRMED", "SUCCESS", "NOT_CONFIRMED", None, None),
    ("TXN-4002", "USR_TEJAS", "Kedarraj Bhimrao Birader", "ICICI Bank", None, 250, 96, "SUCCESS", "P2P", "History", "CONFIRMED", "CONFIRMED", None, "CONFIRMED", "SUCCESS", "NOT_CONFIRMED", None, None),
    ("TXN-4003", "USR_KEDAR", "Book House", "Kotak Bank", "MERCH_BOOK_HOUSE", 450, 120, "SUCCESS", "MERCHANT", "History", "CONFIRMED", "CONFIRMED", "CONFIRMED", "CONFIRMED", "SUCCESS", "NOT_CONFIRMED", None, None),
    ("TXN-4004", "USR_SHIVA", "Tejas Satish Kadam", "HDFC Bank", None, 100, 150, "SUCCESS", "P2P", "History", "CONFIRMED", "CONFIRMED", None, "CONFIRMED", "SUCCESS", "NOT_CONFIRMED", None, None),
]


def seed_local():
    for t in store.TABLES:
        if t != "upi_wa_devices":  # keep paired extension device keys across resets
            store.delete_all(t)
    for u in USERS:
        store.put("upi_users", dict(zip(["user_id", "name", "phone", "email", "bank", "upi_id"], u)))
    now = dt.datetime.now(dt.timezone.utc)
    for i, r in enumerate(TX):
        tid, sender, bn, bb, merch, amt, hrs, app, kind, note, cd, bc, mc, psp, npci, rv, rvd, rcd = r
        store.put("upi_transactions", {"transaction_id": tid, "upi_ref": f"4{int(tid[-4:]) * 7919:011d}", "sender_user_id": sender, "sender_bank": UB[sender][1],
                                       "beneficiary_name": bn, "beneficiary_bank": bb, "merchant_id": merch, "amount": amt,
                                       "ts": (now - dt.timedelta(hours=hrs)).isoformat(timespec="seconds"),
                                       "status": "COMPLETED" if app == "SUCCESS" else "DISPUTABLE", "app_status": app, "kind": kind, "note": note})
        store.put("upi_ground_truth", {"transaction_id": tid, "customer_debit": cd, "beneficiary_credit": bc, "merchant_confirmation": mc,
                                       "psp_status": psp, "npci_status": npci, "reversal_status": rv, "reversal_day": rvd, "reconcile_day": rcd})
    return {"users": len(USERS), "transactions": len(TX)}


def populate_demo():
    """Pre-run the real workflow so every dashboard page has data: cases in every state, complaints, SLA breaches, audit log, WhatsApp chats."""
    import graph, whatsapp
    cases = [  # (user, txn, complaint text, [days to advance])
        ("USR_TEJAS", "TXN-5001", "I sent Rs.500 to Shiva. My bank account was debited but he did not receive the money.", [2]),
        ("USR_KEDAR", "TXN-5002", "I paid Rs.1000 to a merchant. Debited, but the merchant says they never received it.", [4]),
        ("USR_SHIVA", "TXN-5003", "Paytm shows payment failed but Rs.750 was deducted from my account.", [1]),
        ("USR_TEJAS", "TXN-5004", "Payment failed but I think money was deducted.", []),
        ("USR_KEDAR", "TXN-5005", "I sent Rs.1200 and the receiver says nothing arrived.", []),
        ("USR_SHIVA", "TXN-5006", "Sent Rs.250 to Tejas, debited but he has not received it.", [3]),
        ("USR_SHIVA", None, "my money is stuck please help", []),
    ]
    for u, t, text, days in cases:
        s = graph.run_new(text, u, t)
        for d in days:
            graph.run_followup(s["case_id"], d)
    dev = {"id": "dv_demo", "name": "Demo browser", "prefix": "pals_dev_demo...", "key_hash": "-", "status": "revoked", "created": store.now(), "captured": 0, "settings": dict(whatsapp.DEFAULTS)}
    store.put("upi_wa_devices", dev)

    def wa(phone, name, text):
        r = whatsapp.ingest(dev, {"chat": {"name": name, "phone": phone, "saved_name": bool(name)},
                                  "messages": [{"id": "demo:" + store.uid(), "direction": "in", "text": text, "sender": name or phone}]}, process=False)
        return whatsapp.process_chat(r["lead_id"]) if r.get("accepted") else None
    wa("+919820011002", "Tejas Satish Kadam", "hi")
    r = wa("+919820011002", "Tejas Satish Kadam", "Paid Rs.2499 to Chai Point Cafe, money debited but the cafe never got it. TXN-5007")
    wa("+919820011002", "Tejas Satish Kadam", "any update on my money?")
    wa("+919820011003", "Kedarraj Bhimrao Birader", "Paytm shows failed but Rs.90 was deducted. TXN-5008")
    wa("+919820011001", "Shiva Singh", "my money is stuck please help")
    r5 = wa("+919820011001", "Shiva Singh", "Sent Rs.5000 TXN-5009, debited but receiver did not get it")
    wa("+919000000000", "", "my payment failed and money got debited, please check")
    # let some WhatsApp cases age, so SLA states differ
    for cid, d in [(r5 and r5.get("case_id"), 2), (r5 and r5.get("case_id"), 2)]:
        if cid:
            graph.run_followup(cid, d)
    k8 = [c for c in store.all_("upi_disputes") if c["transaction_id"] == "TXN-5008"]
    if k8:
        graph.run_followup(k8[0]["case_id"], 1)
    return {"cases": len(store.all_("upi_disputes")), "chats": len(store.all_("upi_wa_chats"))}


def seed_all():
    r = seed_local()
    r.update(populate_demo())
    return r


if __name__ == "__main__":
    print("local:", seed_all() if "--empty" not in sys.argv else seed_local())
    print("supabase ready:", store.check_supabase(), store.SB["error"] or "")
    if store.SB["ready"]:
        print("mirrored rows queued:", store.sync_all())
        import time; time.sleep(6)
    else:
        print("-> Run upi_swarm/schema.sql once in the Supabase SQL editor, then POST /api/supabase/sync (or re-run seed.py).")
    if "--no-neo4j" not in sys.argv:
        import graph_sync
        try:
            print("neo4j:", graph_sync.seed_neo4j())
        except Exception as e:
            print("neo4j seed failed:", e)
