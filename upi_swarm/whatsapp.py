"""WhatsApp integration. Implements the exact backend contract of the WhatsApp/extension Chrome extension
(/api/ext/ping|config|ingest|debug with X-Device-Key) and bridges captured chats into the dispute workflow."""
import re, json, hashlib, hmac, secrets, threading, os
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import store, graph
from store import now, uid

router = APIRouter()
LOCK = threading.RLock()
_CTX = threading.local()
DEFAULTS = {"capture_enabled": True, "capture_groups": False, "capture_outgoing": True, "min_chars": 1, "blocked": [],
            "scan_unread": True, "unread_mode": "open", "max_chats": 8}
INTENT = re.compile(r"debit|deduct|paid|payment|money|paisa|paise|pese|refund|fail|not receiv|nahi mila|didn.t receiv|transaction|txn|upi|₹|rs\.?\s*\d|reversal|stuck|amount", re.I)
CLOSED = ("RESOLVED", "CLOSED_NO_DEBIT", "SUPERSEDED")
DEBUG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "wa_debug_last.json")


def _settings(dev):
    return {**DEFAULTS, **(dev.get("settings") or {})}


def _dev(request: Request):
    key = request.headers.get("X-Device-Key", "")
    if not key:
        return None
    h = hashlib.sha256(key.encode()).hexdigest()
    for d in store.find("upi_wa_devices", status="active"):
        if hmac.compare_digest(d["key_hash"], h):
            return d
    return None


def _no_auth():
    return JSONResponse({"error": "invalid_device_key"}, 401)


def _digits(s):
    return re.sub(r"\D", "", str(s or ""))


def _match_user(phone, name):
    p = _digits(phone)[-10:]
    for u in store.all_("upi_users"):
        if p and _digits(u["phone"])[-10:] == p:
            return u["user_id"]
    for u in store.all_("upi_users"):
        if name and u["name"].lower() == name.strip().lower():
            return u["user_id"]
    return None


# ------------------------------------------------------------- devices
class NewDevice(BaseModel):
    name: str = "My browser"


@router.post("/api/devices")
def create_device(body: NewDevice):
    key = "pals_dev_" + secrets.token_urlsafe(24)
    d = {"id": uid("dv_"), "name": body.name.strip()[:60] or "My browser", "prefix": key[:13] + "...", "key_hash": hashlib.sha256(key.encode()).hexdigest(),
         "status": "active", "created": now(), "last_seen": None, "captured": 0, "settings": dict(DEFAULTS)}
    store.put("upi_wa_devices", d)
    return {"ok": True, "id": d["id"], "key": key}  # key shown once; only the hash is stored


@router.get("/api/devices")
def list_devices():
    return [{k: v for k, v in d.items() if k != "key_hash"} for d in store.all_("upi_wa_devices", "created DESC")]


@router.post("/api/devices/{dev_id}/revoke")
def revoke(dev_id: str):
    d = store.get("upi_wa_devices", dev_id)
    if not d:
        return JSONResponse({"error": "not_found"}, 404)
    d["status"] = "revoked"
    store.put("upi_wa_devices", d)
    return {"ok": True}


@router.post("/api/devices/{dev_id}/settings")
def dev_settings(dev_id: str, body: dict):
    d = store.get("upi_wa_devices", dev_id)
    if not d:
        return JSONResponse({"error": "not_found"}, 404)
    cur = {**DEFAULTS, **(d.get("settings") or {})}
    for k in ("capture_enabled", "capture_groups", "capture_outgoing", "scan_unread"):
        if k in body:
            cur[k] = bool(body[k])
    if body.get("unread_mode") in ("open", "preview"):
        cur["unread_mode"] = body["unread_mode"]
    if "max_chats" in body:
        cur["max_chats"] = max(1, min(20, int(body["max_chats"])))
    d["settings"] = cur
    store.put("upi_wa_devices", d)
    return {"ok": True, "settings": cur}


# ------------------------------------------------------------- extension endpoints
@router.get("/api/ext/ping")
def ext_ping(request: Request):
    d = _dev(request)
    if not d:
        return _no_auth()
    d["last_seen"] = now(); store.put("upi_wa_devices", d)
    return {"ok": True, "device": d["name"], "server_time": now()}


@router.get("/api/ext/config")
def ext_config(request: Request):
    d = _dev(request)
    if not d:
        return _no_auth()
    d["last_seen"] = now(); store.put("upi_wa_devices", d)
    return {"settings": _settings(d), "device": d["name"]}


@router.post("/api/ext/debug")
async def ext_debug(request: Request):
    if not _dev(request):
        return _no_auth()
    try:
        open(DEBUG_FILE, "w", encoding="utf-8").write(json.dumps(await request.json(), indent=1)[:60000])
    except Exception:
        pass
    return {"ok": True}


@router.post("/api/ext/ingest")
async def ext_ingest(request: Request):
    d = _dev(request)
    if not d:
        return _no_auth()
    data = await request.json()
    out = ingest(d, data)
    return {"ok": True, "data": out}


def ingest(dev, data, process=True):
    s = _settings(dev)
    dev["last_seen"] = now()
    if not s["capture_enabled"]:
        store.put("upi_wa_devices", dev)
        return {"accepted": 0, "skipped": "capture_disabled"}
    chat = data.get("chat") or {}
    if chat.get("is_group") and not s["capture_groups"]:
        return {"accepted": 0, "skipped": "group_chat"}
    phone = _digits(chat.get("phone"))[:15]
    name = str(chat.get("name") or "").strip()[:120]
    key = phone or ("name:" + name.lower())
    if key == "name:":
        return {"accepted": 0, "skipped": "no_identity"}
    with LOCK:
        c = store.get("upi_wa_chats", key)
        if not c and phone and name:  # chat first seen by name only (list preview); now the number is known -> merge
            old = store.get("upi_wa_chats", "name:" + name.lower())
            if old:
                c = {**old, "chat_key": key, "phone": "+" + phone}
                for m in store.find("upi_wa_messages", chat_key=old["chat_key"]):
                    m["chat_key"] = key; store.put("upi_wa_messages", m)
                store.put("upi_wa_chats", c)
        existing = {m["id"].split("|", 1)[-1]: m for m in store.find("upi_wa_messages", chat_key=key)}
        fresh = []
        for m in (data.get("messages") or [])[:200]:
            mid, text = str(m.get("id", ""))[:200], str(m.get("text", "")).strip()[:4000]
            direction = "in" if m.get("direction") == "in" else "out"
            if not mid or mid in existing or len(text) < s["min_chars"] or (direction == "out" and not s["capture_outgoing"]):
                continue
            if direction == "in" and any(e["direction"] == "in" and e["text"] == text and (mid.startswith("preview:") or e["id"].split("|", 1)[-1].startswith("preview:"))
                                         for e in existing.values()):
                continue
            fresh.append({"id": f"{key}|{mid}", "chat_key": key, "direction": direction, "text": text, "sender": str(m.get("sender", ""))[:80],
                          "ts_raw": str(m.get("ts_raw", ""))[:40], "captured": now(), "processed": 0 if direction == "in" else 1, "case_id": None})
        if not fresh:
            store.put("upi_wa_devices", dev)
            return {"accepted": 0, "lead_id": key}
        created = c is None
        if created:
            c = {"chat_key": key, "phone": "+" + phone if phone else None, "name": name or ("+" + phone), "saved_name": 1 if chat.get("saved_name") else 0,
                 "user_id": _match_user(phone, name), "device_id": dev["id"], "message_count": 0}
        elif name and chat.get("saved_name"):
            c["name"] = name
        if not c.get("user_id"):
            c["user_id"] = _match_user(phone, name)
        for m in fresh:
            store.put("upi_wa_messages", m)
        c.update(last_message=fresh[-1]["text"][:140], last_direction=fresh[-1]["direction"], last_seen=now(),
                 message_count=len(store.find("upi_wa_messages", chat_key=key)))
        store.put("upi_wa_chats", c)
        dev["captured"] = (dev.get("captured") or 0) + len(fresh)
        store.put("upi_wa_devices", dev)
    if process and any(m["direction"] == "in" for m in fresh):
        threading.Thread(target=process_chat, args=(key,), daemon=True).start()
    return {"accepted": len(fresh), "lead_id": key, "created": created}


# ------------------------------------------------------------- bridge: chat -> dispute workflow
def _bot_say(chat_key, text, case_id=None):
    store.put("upi_wa_messages", {"id": f"{chat_key}|bot:{uid()}", "chat_key": chat_key, "direction": "out", "text": text,
                                  "sender": "PALS Bot (simulated send)", "ts_raw": "", "captured": now(), "processed": 1, "case_id": case_id})
    c = store.get("upi_wa_chats", chat_key)
    if c:
        c.update(last_message=text[:140], last_direction="out", last_seen=now(), message_count=len(store.find("upi_wa_messages", chat_key=chat_key)))
        store.put("upi_wa_chats", c)


def _chat_cases(chat_key):
    return [d for d in store.all_("upi_disputes", "created_at") if (d.get("state") or {}).get("wa_chat") == chat_key]


def process_chat(chat_key):
    with LOCK:
        c = store.get("upi_wa_chats", chat_key)
        if not c:
            return {"status": "NO_CHAT"}
        pending = [m for m in store.find("upi_wa_messages", order="captured", chat_key=chat_key) if m["direction"] == "in" and not m["processed"]]
        if not pending:
            return {"status": "NOTHING_PENDING"}
        texts = [m["text"] for m in pending]
        if not any(INTENT.search(t) for t in texts):
            for m in pending:
                m["processed"] = 1; store.put("upi_wa_messages", m)
            return {"status": "NO_DISPUTE_INTENT"}
        if not c.get("user_id"):
            return {"status": "UNLINKED"}  # waits until the chat is linked to a Paytm user
        cases = _chat_cases(chat_key)
        live = [d for d in cases if d["status"] not in CLOSED and d["status"] != "AWAITING_CUSTOMER_INFO"]
        asking = [d for d in cases if d["status"] == "AWAITING_CUSTOMER_INFO"]
        description = " ".join(texts)
        mentions_txn = re.search(r"TXN[-_ ]?\d+", description, re.I)
        if live and not mentions_txn:
            d = live[-1]
            _bot_say(chat_key, f"We're already working on your case {d['case_id']} (status: {d['status'].replace('_', ' ').title()}). We'll message you as soon as there's an update.", d["case_id"])
            for m in pending:
                m["processed"] = 1; m["case_id"] = d["case_id"]; store.put("upi_wa_messages", m)
            return {"status": "ALREADY_TRACKING", "case_id": d["case_id"]}
        for d in asking:  # the customer sent the missing details: fold the earlier text in and retire the old stub case
            description = ((d.get("state") or {}).get("description") or "") + " " + description
            d["status"] = "SUPERSEDED"; store.put("upi_disputes", d)
        _CTX.chat = chat_key
        try:
            s = graph.run_new(description, c["user_id"], None, wa_chat=chat_key)
        finally:
            _CTX.chat = None
        for m in pending:
            m["processed"] = 1; m["case_id"] = s["case_id"]; store.put("upi_wa_messages", m)
        replies = [n for n in store.find("upi_notifications", case_id=s["case_id"], recipient="CUSTOMER")]
        if s.get("needs_info") and not replies:
            _bot_say(chat_key, "Please share the transaction ID (e.g. TXN-1234) or UPI reference and the amount so we can investigate.", s["case_id"])
        return {"status": s["status"], "case_id": s["case_id"]}


def mirror_customer_message(case_id, msg):
    """Customer-facing notifications of a case are also posted into the WhatsApp chat the case came from."""
    chat_key = getattr(_CTX, "chat", None)  # set while a WhatsApp-originated run is in flight
    if not chat_key:
        d = store.get("upi_disputes", case_id)
        chat_key = ((d or {}).get("state") or {}).get("wa_chat")
    if chat_key:
        _bot_say(chat_key, msg, case_id)


# ------------------------------------------------------------- dashboard API
@router.get("/api/wa/chats")
def wa_chats():
    out = []
    for c in store.all_("upi_wa_chats", "last_seen DESC"):
        u = store.get("upi_users", c["user_id"]) if c.get("user_id") else None
        out.append({**c, "user_name": u["name"] if u else None, "cases": [{"case_id": d["case_id"], "status": d["status"]} for d in _chat_cases(c["chat_key"])],
                    "pending": len([m for m in store.find("upi_wa_messages", chat_key=c["chat_key"]) if m["direction"] == "in" and not m["processed"]])})
    return out


@router.get("/api/wa/chats/{chat_key:path}/messages")
def wa_messages(chat_key: str):
    return store.find("upi_wa_messages", order="captured", chat_key=chat_key)


class Link(BaseModel):
    chat_key: str
    user_id: str | None = None


@router.post("/api/wa/link")
def wa_link(body: Link):
    c = store.get("upi_wa_chats", body.chat_key)
    if not c:
        return JSONResponse({"error": "not_found"}, 404)
    c["user_id"] = body.user_id or None
    store.put("upi_wa_chats", c)
    return {"ok": True, "result": process_chat(body.chat_key) if body.user_id else None}


class Sim(BaseModel):
    phone: str = ""
    name: str = ""
    text: str


@router.post("/api/wa/simulate")
def wa_simulate(body: Sim):
    """Inject an incoming WhatsApp message without Chrome (for demos/tests). Goes through the same ingest+workflow path."""
    dev = (store.find("upi_wa_devices", status="active") or [None])[0]
    if not dev:
        dev = {"id": "dv_sim", "name": "Simulator", "prefix": "-", "key_hash": "-", "status": "active", "created": now(), "captured": 0, "settings": dict(DEFAULTS)}
        store.put("upi_wa_devices", dev)
    mid = "sim:" + uid()
    r = ingest(dev, {"chat": {"name": body.name, "phone": body.phone, "saved_name": bool(body.name)},
                     "messages": [{"id": mid, "direction": "in", "text": body.text, "sender": body.name or body.phone}]}, process=False)
    return {"ingest": r, "result": process_chat(r["lead_id"]) if r.get("accepted") else None}
