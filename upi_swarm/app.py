"""FastAPI backend for the UPI dispute swarm.   Run: python -m uvicorn app:app --port 8800"""
import os, sys, asyncio, datetime as dt
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import store, graph, seed
from institutions import router as mock_router
from whatsapp import router as wa_router
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="UPI Dispute Swarm")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(mock_router)
app.include_router(wa_router)
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
WORKER = {"last_tick": None, "ticks": 0, "processed": 0}
CLOSED = ("RESOLVED", "CLOSED_NO_DEBIT", "SUPERSEDED")


class NewComplaint(BaseModel):
    user_id: str | None = None
    transaction_id: str | None = None
    description: str = ""


class Advance(BaseModel):
    days: float = 0
    human_approved: bool | None = None


def _slim(s):
    return {k: v for k, v in s.items() if k != "trace"}


@app.on_event("startup")
async def _start():
    if not store.all_("upi_users"):
        seed.seed_local()
    await asyncio.to_thread(store.check_supabase)
    if store.SB["ready"]:
        await asyncio.to_thread(store.sync_all)
    asyncio.create_task(_worker())


async def _worker():
    """Background SLA monitor: re-evaluates open cases whenever the elapsed day changes (no user needs to open the app)."""
    while True:
        await asyncio.sleep(30)
        WORKER["last_tick"] = store.now(); WORKER["ticks"] += 1
        for d in store.all_("upi_disputes"):
            st = d.get("state") or {}
            if d["status"] in CLOSED or d["status"] == "AWAITING_CUSTOMER_INFO":
                continue
            t = store.get("upi_transactions", d["transaction_id"])
            from institutions import elapsed_days
            day = int(elapsed_days(t, st.get("sim_days", 0)))
            if day != st.get("last_checked_day"):
                try:
                    await asyncio.to_thread(graph.run_followup, d["case_id"], 0)
                    WORKER["processed"] += 1
                except Exception as e:
                    print("[worker]", e)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/flow")
def flow():
    return graph.FLOW_DEF


@app.get("/api/users")
def users():
    return store.all_("upi_users")


@app.get("/api/transactions")
def transactions():
    return store.all_("upi_transactions", "ts DESC")


@app.post("/api/complaints")
def new_complaint(body: NewComplaint):
    s = graph.run_new(body.description, body.user_id, body.transaction_id)
    return {"case_id": s["case_id"], "status": s["status"], "trace": s["trace"], "state": _slim(s)}


@app.get("/api/cases")
def cases():
    out = []
    for d in store.all_("upi_disputes", "created_at DESC"):
        st = d.get("state") or {}
        t = store.get("upi_transactions", d["transaction_id"]) or {}
        u = store.get("upi_users", d["user_id"]) or {}
        out.append({**{k: d[k] for k in d if k != "state"}, "amount": t.get("amount"), "customer": u.get("name"),
                    "sla": st.get("sla"), "complaints": store.find("upi_complaints", case_id=d["case_id"])})
    return out


@app.get("/api/cases/{case_id}")
def case(case_id: str):
    d = store.get("upi_disputes", case_id)
    if not d:
        raise HTTPException(404, "case not found")
    t = store.get("upi_transactions", d["transaction_id"]) or {}
    return {"dispute": {k: v for k, v in d.items() if k != "state"}, "state": _slim(d.get("state") or {}), "transaction": t,
            "customer": store.get("upi_users", d["user_id"]),
            "evidence": store.find("upi_evidence", case_id=case_id, order="ts"),
            "complaints": store.find("upi_complaints", case_id=case_id, order="submitted_at"),
            "audit": store.find("upi_audit_logs", case_id=case_id, order="ts"),
            "notifications": store.find("upi_notifications", case_id=case_id, order="ts")}


@app.post("/api/cases/{case_id}/advance")
def advance(case_id: str, body: Advance):
    try:
        s = graph.run_followup(case_id, body.days, body.human_approved)
    except KeyError:
        raise HTTPException(404, "case not found")
    return {"case_id": case_id, "status": s["status"], "trace": s["trace"], "state": _slim(s)}


@app.get("/api/audit")
def audit(limit: int = 150):
    return store.all_("upi_audit_logs", "ts DESC")[:limit]


@app.get("/api/overview")
def overview():
    cs = store.all_("upi_disputes")
    complaints = store.all_("upi_complaints")
    breaches = [c for c in cs if ((c.get("state") or {}).get("sla") or {}).get("status") in ("OVERDUE", "RESOLVED_LATE")]
    secs = []
    for c in cs:
        try:
            secs.append((dt.datetime.fromisoformat(c["updated_at"]) - dt.datetime.fromisoformat(c["created_at"])).total_seconds())
        except Exception:
            pass
    return {"total": len(cs), "open": len([c for c in cs if c["status"] not in CLOSED]), "resolved": len([c for c in cs if c["status"] in CLOSED]),
            "pending_responses": len([c for c in complaints if c["submission_status"] != "RESOLVED"]), "sla_breaches": len(breaches),
            "avg_investigation_secs": round(sum(secs) / len(secs), 1) if secs else 0,
            "worker": WORKER, "supabase": {"ready": store.SB["ready"], "error": store.SB["error"], "mirrored": store.SB["mirrored"]}}


@app.post("/api/reset")
def reset(empty: bool = False):
    r = seed.seed_local() if empty else seed.seed_all()
    if store.SB["ready"]:
        store.sync_all()
    return r


@app.post("/api/supabase/sync")
def sb_sync():
    ok = store.check_supabase()
    return {"ready": ok, "error": store.SB["error"], "rows_queued": store.sync_all() if ok else 0}


@app.post("/api/neo4j/seed")
def neo_seed():
    import graph_sync
    return graph_sync.seed_neo4j()


@app.get("/api/neo4j/user/{user_id}")
def neo_user(user_id: str):
    import graph_sync
    try:
        return graph_sync.case_graph(user_id)
    except Exception as e:
        raise HTTPException(502, f"Neo4j error: {e}")
