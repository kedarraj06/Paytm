"""Optional Gemini wording helper. Always falls back to deterministic text; output is guard-railed."""
import os, json, urllib.request
import store  # loads .env

_state = {"disabled": False}
FORBIDDEN = ("guarantee", "fraud", "will be refunded", "has been refunded", "money has been received")


def polish(prompt, fallback, allow_refund_words=False, timeout=6):
    key, model = os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
    if not key or _state["disabled"] or os.getenv("USE_LLM", "1") == "0":
        return fallback, "template"
    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
        body = json.dumps({"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 220}}).encode()
        req = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = json.load(r)["candidates"][0]["content"]["parts"][0]["text"].strip()
        low = txt.lower()
        if any(w in low for w in FORBIDDEN if not (allow_refund_words and "refund" in w)):
            return fallback, "template (LLM output rejected by guardrail)"
        return txt, f"gemini:{model}"
    except Exception:
        _state["disabled"] = True  # avoid repeated latency when key/model/quota is bad
        return fallback, "template (LLM unavailable)"
