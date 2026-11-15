"""Neo4j: seed fake data and mirror case state (non-destructive MERGE only; existing nodes from other demos are untouched)."""
import os, threading
import store  # loads .env


def driver():
    from neo4j import GraphDatabase
    return GraphDatabase.driver(os.getenv("NEO4J_URI"), auth=(os.getenv("NEO4J_USERNAME", "neo4j"), os.getenv("NEO4J_PASSWORD")))


DB = lambda: os.getenv("NEO4J_DATABASE", "neo4j")


def seed_neo4j():
    users, txns, truth = store.all_("upi_users"), store.all_("upi_transactions"), {g["transaction_id"]: g for g in store.all_("upi_ground_truth")}
    d = driver()
    with d.session(database=DB()) as s:
        s.run("UNWIND $rows AS r MERGE (u:User {id:r.user_id}) SET u.name=r.name, u.phone=r.phone, u.upi_id=r.upi_id, u.bank=r.bank, u.ltv=coalesce(u.ltv, 25000), u.source='upi_swarm'", rows=users)
        banks = sorted({u["bank"] for u in users} | {t["beneficiary_bank"] for t in txns if t["beneficiary_bank"]})
        s.run("UNWIND $b AS n MERGE (:Bank {name:n})", b=banks)
        s.run("UNWIND $b AS n MERGE (:Institution {name:n})", b=["NPCI", "PAYTM_PSP", "REMITTER_BANK_VIA_PSP", "RELEVANT_PSP_OR_BANK_CHANNEL"])
        for m in sorted({t["merchant_id"] for t in txns if t["merchant_id"]}):
            s.run("MERGE (m:Merchant {id:$m}) SET m.name=$m, m.source='upi_swarm'", m=m)
        for t in txns:
            g = truth.get(t["transaction_id"], {})
            s.run("""MERGE (t:Transaction {id:$id}) SET t.amount=$amt, t.upi_ref=$ref, t.kind=$kind, t.app_status=$app, t.customer_debit=$cd,
                     t.beneficiary_credit=$bc, t.reversal=$rv, t.ts=$ts, t.source='upi_swarm'
                     WITH t MATCH (u:User {id:$uid}) MERGE (u)-[:MADE_TRANSACTION]->(t)
                     WITH t, u MATCH (b:Bank {name:u.bank}) MERGE (u)-[:BANKS_WITH]->(b)""",
                  id=t["transaction_id"], amt=t["amount"], ref=t["upi_ref"], kind=t["kind"], app=t["app_status"], cd=g.get("customer_debit"),
                  bc=g.get("beneficiary_credit"), rv=g.get("reversal_status"), ts=t["ts"], uid=t["sender_user_id"])
            if t["merchant_id"]:
                s.run("MATCH (t:Transaction {id:$id}),(m:Merchant {id:$m}) MERGE (t)-[:PAID_TO]->(m)", id=t["transaction_id"], m=t["merchant_id"])
            else:
                s.run("MATCH (t:Transaction {id:$id}),(u:User {name:$n}) MERGE (t)-[:PAID_TO]->(u)", id=t["transaction_id"], n=t["beneficiary_name"])
    d.close()
    return {"users": len(users), "transactions": len(txns)}


def sync_case(case_id):
    c = store.get("upi_disputes", case_id)
    if not c or not c.get("transaction_id"):
        return
    d = driver()
    try:
        with d.session(database=DB()) as s:
            s.run("""MERGE (c:Case {id:$id}) SET c.status=$st, c.category=$cat, c.sla=$sla, c.deadline=$dl, c.updated=$up, c.source='upi_swarm'
                     WITH c MATCH (t:Transaction {id:$tid}) MERGE (c)-[:ABOUT]->(t)
                     WITH c MATCH (u:User {id:$uid}) MERGE (u)-[:RAISED]->(c)""",
                  id=case_id, st=c["status"], cat=c["issue_category"], sla=c["applicable_sla"], dl=c["deadline"], up=c["updated_at"], tid=c["transaction_id"], uid=c["user_id"])
            if c["responsible_institution"] and c["responsible_institution"] != "NONE":
                s.run("MATCH (c:Case {id:$id}) MERGE (i:Institution {name:$i}) MERGE (c)-[:ROUTED_TO]->(i)", id=case_id, i=c["responsible_institution"])
            for k in store.find("upi_complaints", case_id=case_id):
                s.run("MATCH (c:Case {id:$id}) MERGE (k:Complaint {ref:$ref}) SET k.status=$st, k.institution=$inst MERGE (c)-[:HAS_COMPLAINT]->(k)",
                      id=case_id, ref=k["external_reference"], st=k["submission_status"], inst=k["institution"])
    finally:
        d.close()


def sync_case_async(case_id):
    def _run():
        try:
            sync_case(case_id)
        except Exception as e:
            print("[neo4j sync]", e)
    threading.Thread(target=_run, daemon=True).start()


def case_graph(user_id):
    """Nodes/edges around a user for the dashboard."""
    d = driver()
    nodes, edges = {}, []
    try:
        with d.session(database=DB()) as s:
            rows = s.run("""MATCH (u:User {id:$u}) OPTIONAL MATCH (u)-[r1:MADE_TRANSACTION]->(t:Transaction)
                            OPTIONAL MATCH (t)-[r2:PAID_TO]->(x) OPTIONAL MATCH (u)-[r3:RAISED]->(c:Case)-[r4:ABOUT]->(t)
                            OPTIONAL MATCH (c)-[r5:ROUTED_TO]->(i:Institution) OPTIONAL MATCH (c)-[r6:HAS_COMPLAINT]->(k:Complaint)
                            RETURN u,t,x,c,i,k""", u=user_id).data()
        def add(n, kind):
            if n is None: return None
            nid = n.get("id") or n.get("name") or n.get("ref")
            nodes[nid] = {"id": nid, "label": n.get("name") or n.get("ref") or nid, "type": kind, "sub": n.get("status") or n.get("category") or ""}
            return nid
        for r in rows:
            u = add(r["u"], "user"); t = add(r["t"], "txn"); x = add(r["x"], "party"); c = add(r["c"], "case"); i = add(r["i"], "institution"); k = add(r["k"], "complaint")
            for a, b, l in [(u, t, "MADE_TRANSACTION"), (t, x, "PAID_TO"), (u, c, "RAISED"), (c, t, "ABOUT"), (c, i, "ROUTED_TO"), (c, k, "HAS_COMPLAINT")]:
                if a and b and {"from": a, "to": b, "label": l} not in edges:
                    edges.append({"from": a, "to": b, "label": l})
    finally:
        d.close()
    return {"nodes": list(nodes.values()), "edges": edges}
