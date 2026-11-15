/* PALS WhatsApp Capture: content script for web.whatsapp.com.
 *
 * What it captures
 *  1. The chat that is open: new messages appear live as they arrive.
 *  2. Unread chats in the chat list, in one of two modes (set per device in the dashboard):
 *       "open"    visits each unread chat and reads it fully. NOTE: opening a chat marks it as read, so the
 *                 sender sees blue ticks. It never runs while you are typing or clicking around.
 *       "preview" reads only the last-message preview shown in the chat list. Nothing is opened, no read receipts.
 *
 * WhatsApp Web's page structure is not a public API and changes often, so the selectors below are best-effort
 * and may need updating. Message rows carry data-id="<fromMe>_<chatJid>_<msgId>".
 */
(() => {
  const sent = new Set();            // message ids already delivered (the backend also de-duplicates)
  let lastReport = 0, timer = null, busy = false, acting = false, lastUser = Date.now(), cooldownUntil = 0;

  // Visible badge so it is always obvious when capture is running.
  const badge = document.createElement("div");
  Object.assign(badge.style, {
    position: "fixed", left: "12px", bottom: "12px", zIndex: 99999, font: "12px system-ui, sans-serif",
    padding: "4px 10px", borderRadius: "99px", background: "#3b3a8f", color: "#fff", opacity: ".85", pointerEvents: "none",
  });
  document.documentElement.appendChild(badge);
  const setBadge = (text, bad) => { badge.textContent = "PALS · " + text; badge.style.background = bad ? "#b91c1c" : "#3b3a8f"; };
  setBadge("starting");

  // Track real user activity (ignore our own synthetic clicks) so we never take over the screen mid-use.
  ["keydown", "mousedown", "wheel", "touchstart"].forEach((e) =>
    window.addEventListener(e, () => { if (!acting) lastUser = Date.now(); }, { capture: true, passive: true }));

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const digits = (s) => (s || "").replace(/\D/g, "");
  const looksLikePhone = (s) => /^\+?[\d\s\-()]{7,}$/.test((s || "").trim());
  const looksLikeTime = (s) => /^(\d{1,2}:\d{2}(\s?[ap]m)?|yesterday|today|mon|tue|wed|thu|fri|sat|sun|\d{1,2}\/\d{1,2}\/\d{2,4})$/i.test(s.trim());
  const hash = (s) => { let h = 5381; for (const c of s) h = ((h << 5) + h + c.charCodeAt(0)) | 0; return (h >>> 0).toString(36); };
  const ask = (msg) => new Promise((resolve) =>
    chrome.runtime.sendMessage(msg, (r) => resolve(chrome.runtime.lastError ? null : r)));
  async function waitFor(fn, ms = 5000, step = 150) {
    const end = Date.now() + ms;
    while (Date.now() < end) { const v = fn(); if (v) return v; await sleep(step); }
    return null;
  }

  // ------------------------------------------------------------ open chat
  // Selectors avoid the layout wrappers (#main etc.) that WhatsApp renames; they rely on message markers instead.
  const mainPane = () => document.querySelector("#main") || document.querySelector('[data-testid="conversation-panel-wrapper"]') ||
    [...document.querySelectorAll("header")].map((h) => h.parentElement).find((p) => p && !p.closest("#side, #pane-side")) || null;
  const currentTitle = () => {
    const h = mainPane()?.querySelector("header") || [...document.querySelectorAll("header")].find((x) => !x.closest("#side, #pane-side"));
    return (h?.querySelector("span[title]")?.getAttribute("title") || h?.innerText?.split("\n")[0] || "").trim();
  };
  const baselined = new Set();       // chats whose existing (already read) messages were skipped

  // Every text message in the open chat, oldest first. Works from [data-pre-plain-text], which WhatsApp keeps stable.
  function readMessages() {
    const pane = mainPane() || document;
    const out = [];
    for (const el of pane.querySelectorAll("[data-pre-plain-text]")) {
      const pre = el.getAttribute("data-pre-plain-text") || "";
      const text = (el.querySelector("span.selectable-text, span[dir]")?.innerText || el.innerText || "").trim();
      if (!text) continue;
      const row = el.closest("[data-id]");
      const rid = row?.getAttribute("data-id") || "";
      const out_ = rid ? rid.startsWith("true_") : !!el.closest(".message-out");
      out.push({
        id: rid || "h" + hash(pre + "|" + text), direction: out_ ? "out" : "in", text,
        sender: (pre.split("] ")[1] || "").replace(/:\s*$/, "").trim(), ts_raw: (pre.match(/\[(.*?)\]/) || [])[1] || "",
        jid: rid.split("_")[1] || "",
      });
    }
    return out;
  }

  function chatMeta(jid) {
    const title = currentTitle();
    // 1:1 chats expose the number in the id; newer "@lid" ids do not, so fall back to the title when it is a number.
    const phone = jid.endsWith("@c.us") ? jid.split("@")[0] : (looksLikePhone(title) ? digits(title) : "");
    return { name: title, phone, is_group: jid.endsWith("@g.us"), saved_name: !!title && !looksLikePhone(title) };
  }

  /* Unread only. limit = number of unread messages (from the list badge): send just the last `limit` incoming ones.
     No limit = the chat is open on screen: the first look records what is already there (read), and only messages
     that arrive afterwards are sent. */
  async function scanOpenChat(limit) {
    const all = readMessages();
    const title = currentTitle();
    if (!all.length || !title) return null;
    let pick;
    if (limit) {
      pick = all.filter((m) => m.direction === "in").slice(-limit);
    } else if (!baselined.has(title)) {
      baselined.add(title);
      all.forEach((m) => sent.add(title + "|" + m.id));
      return { sent: 0 };
    } else {
      pick = all.filter((m) => m.direction === "in" && !sent.has(title + "|" + m.id));
    }
    pick = pick.filter((m) => !sent.has(title + "|" + m.id));
    if (!pick.length) return { sent: 0 };
    const jid = (all.find((m) => m.jid) || {}).jid || "";
    const res = await ask({ type: "ingest", payload: { chat: chatMeta(jid), messages: pick.map(({ jid: _j, ...m }) => m) } });
    if (res && res.ok) {
      all.forEach((m) => sent.add(title + "|" + m.id));
      return { sent: res.data.accepted, skipped: res.data.skipped };
    }
    return { error: res?.error || "send failed" };
  }

  // ----------------------------------------------------------- chat list
  const listRows = () => [...document.querySelectorAll('#pane-side [role="listitem"], #pane-side [role="row"]')];
  const isUnread = (row) => !!row.querySelector('[aria-label*="unread" i]');

  function rowInfo(row) {
    const lines = (row.innerText || "").split("\n").map((l) => l.trim()).filter(Boolean);
    const title = (row.querySelector("span[title]")?.getAttribute("title") || lines[0] || "").trim();
    const time = lines.find((l) => looksLikeTime(l)) || "";
    const preview = [...lines].reverse().find((l) => l !== title && !/^\d+$/.test(l) && !looksLikeTime(l)) || "";
    const label = row.querySelector('[aria-label*="unread" i]')?.getAttribute("aria-label") || "";
    return { title, time, preview, count: parseInt(digits(label), 10) || 1, groupLike: /^[^:]{1,40}:\s/.test(preview) };
  }

  async function openRow(row, title) {
    acting = true;
    try {
      const target = row.querySelector('[role="gridcell"]') || row;
      for (const t of ["mousedown", "mouseup", "click"]) target.dispatchEvent(new MouseEvent(t, { bubbles: true, cancelable: true, view: window }));
    } finally { acting = false; }
    const ok = await waitFor(() => {
      const now = currentTitle();
      return now && (now === title || now.includes(title) || title.includes(now)) && document.querySelector("#main div[data-id]");
    });
    if (ok) await sleep(700);        // let the messages render
    return !!ok;
  }

  async function sweepPreview(rows, conf) {
    let n = 0;
    for (const row of rows.slice(0, conf.settings.max_chats)) {
      const i = rowInfo(row);
      if (!i.preview || (i.groupLike && !conf.settings.capture_groups)) continue;
      const id = "preview:" + i.title + ":" + hash(i.preview + "|" + i.count);
      if (sent.has(id)) continue;
      const res = await ask({ type: "ingest", payload: {
        chat: { name: i.title, phone: looksLikePhone(i.title) ? digits(i.title) : "", is_group: false, saved_name: !looksLikePhone(i.title) },
        messages: [{ id, direction: "in", text: i.preview, ts_raw: i.time }] } });
      if (res && res.ok) { sent.add(id); n += res.data.accepted; }
    }
    return n;
  }

  async function sweepOpen(rows, conf) {
    if (Date.now() - lastUser < 15000) return sweepPreview(rows, conf);   // you are using WhatsApp: do not open chats
    const back = currentTitle();
    let n = 0;
    for (const row of rows.slice(0, conf.settings.max_chats)) {
      const i = rowInfo(row);
      setBadge("reading unread: " + i.title.slice(0, 18));
      if (!(await openRow(row, i.title))) continue;
      const r = await scanOpenChat(i.count);
      if (r && r.sent) n += r.sent;
      if (Date.now() - lastUser < 3000) return n;                // you came back: stop and leave the screen alone
    }
    if (back && currentTitle() !== back) {                       // return to the chat you were looking at
      const prev = listRows().find((r) => rowInfo(r).title === back);
      if (prev) await openRow(prev, back);
    }
    return n;
  }


  // ------------------------------------------------------------ diagnostics
  const log = [];
  const note_ = (m) => { log.push(new Date().toISOString().slice(11, 19) + " " + m); if (log.length > 40) log.shift(); console.log("[PALS]", m); };
  async function report(extra) {
    const q = (sel) => document.querySelectorAll(sel).length;
    const rows = listRows();
    await ask({ type: "debug", payload: {
      at: new Date().toISOString(), url: location.pathname,
      counts: { pane_side: q("#pane-side"), main: q("#main"), header: q("header"), data_id: q("[data-id]"),
                pre_plain: q("[data-pre-plain-text]"), selectable: q("span.selectable-text"), listitem: q('#pane-side [role="listitem"]'),
                row: q('#pane-side [role="row"]'), unread_label: q('[aria-label*="unread" i]'), gridcell: q('#pane-side [role="gridcell"]') },
      open_title: currentTitle(), sample_data_ids: [...document.querySelectorAll("[data-id]")].slice(0, 5).map((e) => e.getAttribute("data-id").replace(/\d{6,}/g, "#")),
      unread_labels: [...document.querySelectorAll('[aria-label*="unread" i]')].slice(0, 5).map((e) => e.getAttribute("aria-label")),
      list: rows.slice(0, 12).map((r) => ({ unread: isUnread(r), ...rowInfo(r), preview: rowInfo(r).preview.slice(0, 30) })),
      messages_in_open_chat: readMessages().length, sent_cache: sent.size, log, ...extra } });
  }

  // --------------------------------------------------------------- driver
  async function run() {
    if (busy) return;
    const conf = await ask({ type: "config" });
    if (!conf || !conf.ok) return setBadge(conf?.error === "not_configured" ? "not connected" : "backend unreachable", true);
    if (conf.paused || !conf.settings.capture_enabled) return setBadge("paused");
    busy = true;
    try {
      const open = await scanOpenChat();
      if (open?.error) return setBadge(open.error, true);
      let note = open ? (open.skipped ? "skipped (" + open.skipped + ")" : "sent " + open.sent + " new") : "open a chat";

      const unread = conf.settings.scan_unread ? listRows().filter(isUnread) : [];
      if (unread.length && Date.now() >= cooldownUntil) {
        const n = conf.settings.unread_mode === "preview" ? await sweepPreview(unread, conf) : await sweepOpen(unread, conf);
        cooldownUntil = Date.now() + 10000;
        note_("sweep " + conf.settings.unread_mode + ": " + n + " new from " + unread.length + " unread rows");
        note = "unread: " + n + " new from " + Math.min(unread.length, conf.settings.max_chats) + " chats";
      } else if (unread.length) {
        note += " · " + unread.length + " unread waiting";
      }
      setBadge(note);
      if (Date.now() - lastReport > 15000) { lastReport = Date.now(); await report({ note }); }
    } finally { busy = false; }
  }

  const schedule = () => { clearTimeout(timer); timer = setTimeout(run, 1500); };
  new MutationObserver(schedule).observe(document.body, { childList: true, subtree: true });   // live messages and list changes
  setInterval(run, 20000);                                                                       // safety net
  chrome.runtime.onMessage.addListener((msg) => { if (msg.type === "scan") { cooldownUntil = 0; lastUser = 0; run(); } });
  schedule();
})();
