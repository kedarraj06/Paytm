"""Dual store: SQLite is the runtime source of truth; every write is mirrored to Supabase (upi_* tables) in a background thread."""
import os, json, sqlite3, threading, queue, uuid, datetime as dt
from dotenv import load_dotenv

BASE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE, "..", ".env"))
from dotenv import dotenv_values
for _k, _v in dotenv_values(os.path.join(BASE, ".env")).items():  # upi_swarm/.env overrides, but untouched placeholders are ignored
    if _v and not any(x in _v for x in ("your-", "paste-")):
        os.environ[_k] = _v
DB_PATH = os.path.join(BASE, "data", "upi.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

TABLES = {
    "upi_users": ("user_id", ["user_id", "name", "phone", "email", "bank", "upi_id"]),
    "upi_transactions": ("transaction_id", ["transaction_id", "upi_ref", "sender_user_id", "sender_bank", "beneficiary_name",
                                            "beneficiary_bank", "merchant_id", "amount", "ts", "status", "app_status", "kind", "note"]),
    "upi_ground_truth": ("transaction_id", ["transaction_id", "customer_debit", "beneficiary_credit", "merchant_confirmation",
                                            "psp_status", "npci_status", "reversal_status", "reversal_day", "reconcile_day"]),
    "upi_disputes": ("case_id", ["case_id", "transaction_id", "user_id", "issue_category", "responsible_institution", "status",
                                 "applicable_sla", "deadline", "created_at", "updated_at", "state"]),
    "upi_evidence": ("evidence_id", ["evidence_id", "case_id", "source_institution", "evidence_type", "evidence_status", "ts", "response_payload"]),
    "upi_complaints": ("complaint_id", ["complaint_id", "case_id", "institution", "external_reference", "submission_status",
                                        "response", "submitted_at", "dedupe_key"]),
    "upi_audit_logs": ("log_id", ["log_id", "case_id", "agent_name", "action", "input_summary", "output_summary", "label", "ts"]),
    "upi_wa_devices": ("id", ["id", "name", "prefix", "key_hash", "status", "created", "last_seen", "captured", "settings"]),
    "upi_wa_chats": ("chat_key", ["chat_key", "phone", "name", "saved_name", "user_id", "device_id", "last_message", "last_direction", "last_seen", "message_count"]),
    "upi_wa_messages": ("id", ["id", "chat_key", "direction", "text", "sender", "ts_raw", "captured", "processed", "case_id"]),
    "upi_notifications": ("notification_id", ["notification_id", "case_id", "recipient", "message", "delivery_status", "ts"]),
}
JSON_COLS = {"state", "response_payload", "response", "settings"}
_lock = threading.RLock()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:10]


def _conn():
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _lock, _conn() as c:
        for t, (pk, cols) in TABLES.items():
            c.execute(f"CREATE TABLE IF NOT EXISTS {t} ({', '.join(cols)}, PRIMARY KEY({pk}))")


def _dec(row):
    d = dict(row)
    for k in JSON_COLS & d.keys():
        if isinstance(d[k], str):
            try:
                d[k] = json.loads(d[k])
            except Exception:
                pass
    return d


# ---------------- Supabase mirror ----------------
SB = {"ready": False, "error": "not checked", "client": None, "mirrored": 0}
_q = queue.Queue()


def _client():
    if SB["client"] is None:
        from supabase import create_client
        SB["client"] = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SECRET_KEY") or os.getenv("SUPABASE_PUBLISHABLE_KEY"))
    return SB["client"]


def check_supabase():
    try:
        _client().table("upi_users").select("user_id").limit(1).execute()
        SB.update(ready=True, error=None)
    except Exception as e:
        SB.update(ready=False, error=str(e)[:200])
    return SB["ready"]


def _mirror_worker():
    while True:
        t, row = _q.get()
        if not SB["ready"]:
            continue
        try:
            _client().table(t).upsert(row).execute()
            SB["mirrored"] += 1
        except Exception as e:
            SB["error"] = str(e)[:200]


threading.Thread(target=_mirror_worker, daemon=True).start()


def _mirror_row(table, row):
    out = {}
    for k, v in row.items():
        if k in JSON_COLS and isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                pass
        out[k] = v
    if SB["ready"]:
        _q.put((table, out))


def sync_all():
    n = 0
    for t in TABLES:
        for r in all_(t):
            _mirror_row(t, r)
            n += 1
    return n


# ---------------- CRUD ----------------
def put(table, row):
    pk, cols = TABLES[table]
    data = {c: row.get(c) for c in cols}
    for k in JSON_COLS & data.keys():
        if data[k] is not None and not isinstance(data[k], str):
            data[k] = json.dumps(data[k], default=str)
    with _lock, _conn() as c:
        c.execute(f"INSERT OR REPLACE INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [data[c_] for c_ in cols])
    _mirror_row(table, data)
    return row


def get(table, key):
    pk = TABLES[table][0]
    with _lock, _conn() as c:
        r = c.execute(f"SELECT * FROM {table} WHERE {pk}=?", (key,)).fetchone()
    return _dec(r) if r else None


def find(table, order=None, **where):
    q = f"SELECT * FROM {table}"
    if where:
        q += " WHERE " + " AND ".join(f"{k}=?" for k in where)
    if order:
        q += f" ORDER BY {order}"
    with _lock, _conn() as c:
        return [_dec(r) for r in c.execute(q, list(where.values())).fetchall()]


def all_(table, order=None):
    return find(table, order)


def delete_all(table):
    with _lock, _conn() as c:
        c.execute(f"DELETE FROM {table}")


init_db()
