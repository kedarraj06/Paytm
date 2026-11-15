// PALS WhatsApp Capture: service worker. Talks to the PALS backend with the device key.
// The key is kept in chrome.storage.local and is only ever sent to the backend URL the user entered.

async function conn() {
  const { backendUrl = "", deviceKey = "" } = await chrome.storage.local.get(["backendUrl", "deviceKey"]);
  return { base: backendUrl.replace(/\/+$/, ""), key: deviceKey.trim() };
}

async function api(path, body) {
  const { base, key } = await conn();
  if (!base || !key) throw new Error("not_configured");
  const res = await fetch(base + path, {
    method: body ? "POST" : "GET",
    headers: { "Content-Type": "application/json", "X-Device-Key": key },
    body: body ? JSON.stringify(body) : undefined,
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || "http_" + res.status);
  return json;
}

let cached = null, cachedAt = 0;
async function getConfig(force) {
  if (!force && cached && Date.now() - cachedAt < 60000) return cached;
  const { paused = false } = await chrome.storage.local.get("paused");
  const cfg = await api("/api/ext/config");
  cached = { settings: cfg.settings, device: cfg.device, paused };
  cachedAt = Date.now();
  setBadge(paused || !cfg.settings.capture_enabled ? "OFF" : "ON", paused || !cfg.settings.capture_enabled ? "#64748b" : "#15803d");
  return cached;
}

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
}

const handlers = {
  async config() {
    try { return { ok: true, ...(await getConfig(false)) }; }
    catch (e) { setBadge("!", "#b91c1c"); return { ok: false, error: e.message }; }
  },
  async ingest({ payload }) {
    const out = await api("/api/ext/ingest", payload);
    const lastSync = { at: Date.now(), accepted: out.data.accepted, skipped: out.data.skipped || null };
    await chrome.storage.local.set({ lastSync });
    return { ok: true, data: out.data };
  },
  async debug({ payload }) { await api("/api/ext/debug", payload); return { ok: true }; },
  async test() {
    const out = await api("/api/ext/ping");
    await getConfig(true);
    return { ok: true, device: out.device };
  },
  async refresh() { return { ok: true, ...(await getConfig(true)) }; },
};

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const fn = handlers[msg.type];
  if (!fn) return false;
  fn(msg).then(sendResponse).catch((e) => sendResponse({ ok: false, error: e.message }));
  return true; // async response
});

chrome.alarms.create("refresh-config", { periodInMinutes: 5 });
chrome.alarms.onAlarm.addListener(() => handlers.config());
chrome.runtime.onInstalled.addListener(() => setBadge("", "#64748b"));
chrome.storage.onChanged.addListener((c) => { if (c.paused || c.backendUrl || c.deviceKey) { cached = null; handlers.config(); } });
