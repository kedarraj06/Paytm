const $ = (s, r = document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const api = async (p, o) => { const r = await fetch(p, o && { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(o) }); if (!r.ok) throw new Error(await r.text()); return r.json(); };
const chip = s => `<span class="chip ${esc(s)}">${esc(String(s).replaceAll("_", " "))}</span>`;
const LBL = { "AI recommendation": "ai", "Verified evidence": "ev", "Simulated API response": "sim", "Approved action": "ok", "Pending human approval": "hu" };
const badge = l => `<span class="badge b-${LBL[l] || "ev"}">${esc(l)}</span>`;
const inr = n => "₹" + Number(n || 0).toLocaleString("en-IN");

let USERS = [], TXNS = [], FLOW = null, CASES = [], currentCase = null, pos = {}, nodeTrace = {};
const SCEN = [
  ["S1 · Failed transfer ₹500", "USR_TEJAS", "TXN-5001", "I sent ₹500 to a friend. My bank account was debited but he did not receive the money."],
  ["S2 · Failed merchant ₹1,000", "USR_KEDAR", "TXN-5002", "I paid ₹1000 to a merchant. My bank account was debited, but the merchant didn't receive the money."],
  ["S3 · App says failed, merchant paid ₹750", "USR_SHIVA", "TXN-5003", "Paytm shows payment failed but ₹750 was deducted from my account."],
  ["Edge · Never debited", "USR_TEJAS", "TXN-5004", "Payment failed but I think money was deducted."],
  ["Edge · Conflicting records", "USR_KEDAR", "TXN-5005", "I sent ₹1200 and the receiver says nothing arrived."],
  ["Edge · Missing info", "USR_SHIVA", "", "my money is stuck please help"],
];

// ---------------- tabs ----------------
document.querySelectorAll("#nav button").forEach(b => b.onclick = () => show(b.dataset.tab));
function show(t) {
  document.querySelectorAll("#nav button").forEach(x => x.classList.toggle("on", x.dataset.tab === t));
  document.querySelectorAll(".tab").forEach(x => x.classList.toggle("on", x.id === "tab-" + t));
  ({ whatsapp: loadWa, overview: loadOverview, case: loadCaseList, complaints: loadComplaints, sla: loadSla, activity: loadActivity }[t] || (() => { }))();
}

// ---------------- boot ----------------
async function boot() {
  [USERS, TXNS, FLOW] = await Promise.all([api("/api/users"), api("/api/transactions"), api("/api/flow")]);
  for (const sel of ["#f-user", "#n-user"]) $(sel).innerHTML = USERS.map(u => `<option value="${u.user_id}">${esc(u.name)}</option>`).join("");
  for (const [u, t] of [["#f-user", "#f-txn"], ["#n-user", "#n-txn"]]) { $(u).onchange = () => fillTxns(u, t); fillTxns(u, t); }
  $("#f-scen").innerHTML = '<span class="lab">Scenarios</span>' + SCEN.map((s, i) => `<button data-i="${i}">${esc(s[0])}</button>`).join("");
  $("#f-scen").onclick = e => { const i = e.target.dataset.i; if (i === undefined) return; const [, u, t, d] = SCEN[i]; $("#f-user").value = u; fillTxns("#f-user", "#f-txn"); $("#f-txn").value = t; $("#f-desc").value = d; };
  $("#f-run").onclick = () => runFlow($("#f-user").value, $("#f-txn").value, $("#f-desc").value, "#f-run");
  $("#f-sample").onclick = runSample;
  $("#n-submit").onclick = submitNew;
  $("#c-select").onchange = () => loadCase($("#c-select").value);
  $("#c-refresh").onclick = () => loadCase($("#c-select").value);
  initFlow(); refreshSys(); setInterval(refreshSys, 15000);
}
function fillTxns(u, t) {
  const uid = $(u).value;
  $(t).innerHTML = `<option value="">(none – let router find it)</option>` + TXNS.filter(x => x.sender_user_id === uid)
    .map(x => `<option value="${x.transaction_id}">${x.transaction_id} · ${inr(x.amount)} → ${esc(x.beneficiary_name)} · app:${x.app_status}</option>`).join("");
}
async function refreshSys() {
  try {
    const o = await api("/api/overview");
    $("#f-stats").innerHTML = [[o.total, "cases"], [o.open, "open"], [o.resolved, "resolved"], [o.sla_breaches, "SLA breaches"], [o.pending_responses, "awaiting institutions"], [o.avg_investigation_secs + "s", "avg investigation"]]
      .map(([v, l]) => `<div class="stat"><b>${v}</b><span>${l}</span></div>`).join("");
    $("#sysbox").innerHTML = `Supabase mirror: <b style="color:${o.supabase.ready ? "var(--gr)" : "var(--am)"}">${o.supabase.ready ? "LIVE (" + o.supabase.mirrored + " rows)" : "tables missing"}</b><br>SLA worker ticks: <b>${o.worker.ticks}</b><br>Cases re-checked: <b>${o.worker.processed}</b>`;
  } catch { }
}

// ---------------- agent flow canvas ----------------
const W = 232, H = 74, VW = 1200, VH = 720, svgNS = "http://www.w3.org/2000/svg";
const dim = {}, orig = {};
let expanded = null, playToken = 0, edgeState = {};
const dm = id => dim[id] || { w: W, h: H };

function initFlow() {
  FLOW.nodes.forEach(n => pos[n.id] = { x: n.x, y: n.y });
  const B = $("#blocks"); B.innerHTML = "";
  FLOW.nodes.forEach(n => {
    const g = document.createElementNS(svgNS, "g"); g.setAttribute("class", `blk ${n.kind}`); g.dataset.id = n.id;
    g.innerHTML = `<rect class="body" width="${W}" height="${H}" rx="37"/><text class="t" x="34" y="29">${esc(n.title)}</text><text class="s" x="34" y="46" id="sub-${n.id}">idle</text><text class="s" x="34" y="60" id="st-${n.id}"></text><g class="det" id="det-${n.id}"></g>
      <circle class="port" data-s="l" r="6" cx="0" cy="${H / 2}"/><circle class="port" data-s="r" r="6" cx="${W}" cy="${H / 2}"/><circle class="port" data-s="t" r="6" cx="${W / 2}" cy="0"/><circle class="port" data-s="b" r="6" cx="${W / 2}" cy="${H}"/>`;
    B.appendChild(g); drag(g, n.id);
    g.onclick = () => { inspect(n.id); toggle(n.id); };
  });
  place(); drawEdges();
}
function place() { document.querySelectorAll(".blk").forEach(g => { const p = pos[g.dataset.id]; g.setAttribute("transform", `translate(${p.x},${p.y})`); }); }
function ctr(id) { const d = dm(id); return { x: pos[id].x + d.w / 2, y: pos[id].y + d.h / 2 }; }
function port(a, b) {
  const A = ctr(a), Bc = ctr(b), dx = Bc.x - A.x, dy = Bc.y - A.y, da = dm(a), db = dm(b);
  const pt = (id, side) => { const d = dm(id); return { x: pos[id].x + (side === "r" ? d.w : side === "l" ? 0 : d.w / 2), y: pos[id].y + (side === "b" ? d.h : side === "t" ? 0 : d.h / 2), side }; };
  if (Math.abs(dx) > Math.abs(dy) * 0.9 || Math.abs(dy) < (da.h + db.h) / 2) return dx > 0 ? [pt(a, "r"), pt(b, "l")] : [pt(a, "l"), pt(b, "r")];
  return dy > 0 ? [pt(a, "b"), pt(b, "t")] : [pt(a, "t"), pt(b, "b")];
}
function drawEdges() {
  const E = $("#edges"); E.innerHTML = "";
  FLOW.edges.forEach(([a, b, label]) => {
    const [p, q] = port(a, b), h = p.side === "r" || p.side === "l";
    const c = h ? Math.max(40, Math.abs(q.x - p.x) / 2) : Math.max(40, Math.abs(q.y - p.y) / 2);
    const c1 = h ? { x: p.x + (p.side === "r" ? c : -c), y: p.y } : { x: p.x, y: p.y + (p.side === "b" ? c : -c) };
    const c2 = h ? { x: q.x + (q.side === "l" ? -c : c), y: q.y } : { x: q.x, y: q.y + (q.side === "t" ? -c : c) };
    const path = document.createElementNS(svgNS, "path"), k = edgeState[a + ">" + b];
    path.setAttribute("d", `M${p.x},${p.y} C${c1.x},${c1.y} ${c2.x},${c2.y} ${q.x},${q.y}`);
    path.setAttribute("class", "edge" + (k ? " " + k : "")); path.setAttribute("marker-end", k === "on" ? "url(#arr-on)" : "url(#arr)");
    E.appendChild(path);
    if (label) { const t = document.createElementNS(svgNS, "text"); t.setAttribute("class", "elabel"); t.setAttribute("x", (p.x + q.x) / 2 + 4); t.setAttribute("y", (p.y + q.y) / 2 - 5); t.textContent = label; E.appendChild(t); }
  });
}
function drag(g, id) {
  g.addEventListener("pointerdown", e => {
    const svg = $("#flow"), pt = svg.createSVGPoint(); let moved = false;
    const to = ev => { pt.x = ev.clientX; pt.y = ev.clientY; return pt.matrixTransform(svg.getScreenCTM().inverse()); };
    const s = to(e), o = { ...pos[id] }; g.setPointerCapture(e.pointerId); g.style.cursor = "grabbing";
    const mv = ev => { const c = to(ev); if (Math.abs(c.x - s.x) + Math.abs(c.y - s.y) > 3) moved = true; const d = dm(id); pos[id] = { x: Math.max(0, Math.min(VW - d.w, o.x + c.x - s.x)), y: Math.max(0, Math.min(VH - d.h, o.y + c.y - s.y)) }; if (orig[id]) orig[id] = null; place(); drawEdges(); };
    const up = () => { g.removeEventListener("pointermove", mv); g.removeEventListener("pointerup", up); g.style.cursor = "grab"; if (moved) { g.onclick = null; setTimeout(() => g.onclick = () => { inspect(id); toggle(id); }, 0); } };
    g.addEventListener("pointermove", mv); g.addEventListener("pointerup", up);
  });
}
function setNode(id, cls, sub, st) {
  const g = document.querySelector(`.blk[data-id="${id}"]`); if (!g) return;
  g.classList.remove("running", "done", "skipped", "waiting"); if (cls) g.classList.add(cls);
  if (sub !== undefined) $(`#sub-${id}`).textContent = sub.length > 36 ? sub.slice(0, 35) + "…" : sub;
  if (st !== undefined) $(`#st-${id}`).textContent = st.length > 34 ? st.slice(0, 33) + "…" : st;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ----- expand / collapse blocks -----
function setDim(id, w, h) {
  dim[id] = { w, h };
  const g = document.querySelector(`.blk[data-id="${id}"]`), r = g.querySelector("rect.body");
  r.setAttribute("width", w); r.setAttribute("height", h); r.setAttribute("rx", h > H ? 30 : 37);
  const set = (s, cx, cy) => { const c = g.querySelector(`.port[data-s="${s}"]`); c.setAttribute("cx", cx); c.setAttribute("cy", cy); };
  set("l", 0, h / 2); set("r", w, h / 2); set("t", w / 2, 0); set("b", w / 2, h);
  place(); drawEdges();
}
function expand(id, lineCount) {
  if (expanded && expanded !== id) collapse(expanded, true);
  const w = 336, h = 56 + lineCount * 15 + 16;
  if (!orig[id]) orig[id] = { ...pos[id] };
  pos[id] = { x: Math.max(8, Math.min(pos[id].x, VW - w - 8)), y: Math.max(8, Math.min(pos[id].y, VH - h - 8)) };
  const g = document.querySelector(`.blk[data-id="${id}"]`); $("#blocks").appendChild(g);
  g.classList.add("open"); setDim(id, w, h); expanded = id;
  $(`#sub-${id}`).style.display = "none"; $(`#st-${id}`).style.display = "none";
}
function collapse(id, keepLines) {
  const g = document.querySelector(`.blk[data-id="${id}"]`); if (!g) return;
  if (orig[id]) { pos[id] = orig[id]; orig[id] = null; }
  g.classList.remove("open"); setDim(id, W, H);
  $(`#det-${id}`).innerHTML = ""; $(`#sub-${id}`).style.display = ""; $(`#st-${id}`).style.display = "";
  if (expanded === id) expanded = null;
}
function addLine(id, text, kind, idx) {
  const t = document.createElementNS(svgNS, "text");
  t.setAttribute("class", "dl " + (kind || "")); t.setAttribute("x", 26); t.setAttribute("y", 62 + idx * 15);
  t.textContent = text.length > 54 ? text.slice(0, 53) + "…" : text; $(`#det-${id}`).appendChild(t);
}
function toggle(id) {
  if (expanded === id) { collapse(id); return; }
  const items = nodeTrace[id]; if (!items) return;
  const lines = stepsFor(items[items.length - 1]); expand(id, lines.length);
  lines.forEach((l, i) => addLine(id, l[0], l[1], i));
}

// ----- what each agent is doing, derived from its real output -----
function stepsFor(t) {
  const o = t.output || {}, L = [], s = x => x === "CONFIRMED" ? "✓" : x === "NOT_CONFIRMED" ? "✗" : x === "PENDING" ? "…" : "?";
  const clean = x => String(x ?? "-").replaceAll("_", " ");
  switch (t.node) {
    case "router": {
      const c = o.claims || {};
      L.push(["Reading the complaint text"]);
      L.push([`Transaction ${o.transaction_id || "not found"} · ${o.amount ? inr(o.amount) : "amount unknown"}`]);
      L.push([`Customer claims (unverified): ${[c.customer_debit_claim && "debited", c.merchant_credit_claim === false && "not received", c.app_shows_failed_claim && "app shows failed"].filter(Boolean).join(", ") || "none"}`]);
      if (o.missing_information?.length) L.push([`Missing: ${o.missing_information.map(clean).join(", ")}`, "bad"]);
      L.push([`Issue type: ${clean(o.issue_type)}`]); L.push([`Case ${o.case_id} opened`, "final"]); break;
    }
    case "investigator":
      L.push([`→ Paytm record: app shows ${o.paytm_app_status}`]);
      L.push([`→ Customer bank: debit ${s(o.customer_debit)} ${clean(o.customer_debit)}`]);
      L.push([`→ Beneficiary bank: credit ${s(o.beneficiary_credit)} ${clean(o.beneficiary_credit)}`]);
      L.push([`→ PSP: ${s(o.psp_status)} ${clean(o.psp_status)}   → NPCI: ${s(o.npci_status)} ${clean(o.npci_raw || o.npci_status)}`]);
      if (o.kind === "MERCHANT") L.push([`→ Merchant/acquirer: ${s(o.merchant_confirmation)} ${clean(o.merchant_confirmation)}`]);
      L.push([`→ Reversal ledger: ${s(o.reversal_status)} ${clean(o.reversal_status)}`]);
      (o.conflicts || []).forEach(c => L.push([`⚠ ${c}`, "bad"]));
      L.push([`Last confirmed state: ${clean(o.last_confirmed_state)}`, "final"]);
      L.push([`Evidence confidence: ${o.evidence_confidence}${o.missing_evidence?.length ? " · missing " + o.missing_evidence.length : ""}`]); break;
    case "resolution":
      L.push(["Matching evidence against dispute rules"]); L.push([`Category: ${clean(o.issue_category)}`]); L.push([`Why: ${o.reason}`]);
      L.push([`Next party: ${clean(o.responsible_next_party)}`]); L.push([`Channel: ${o.recommended_channel || "none"} · ${clean(o.recommended_action)}`]);
      L.push([`Timeline: ${o.applicable_sla}${o.requires_human_review ? " · needs human review" : ""}`, "final"]); break;
    case "policy":
      (o.checks || []).forEach(c => L.push([`${c.passed ? "✓" : "✗"} ${clean(c.check)}`, c.passed ? "" : "bad"]));
      L.push([`Decision: ${clean(o.decision)}`, "final"]); break;
    case "complaint_mgmt": {
      const p = o.response?.payload;
      if (p) { L.push(["Drafting structured complaint"]); L.push([`Subject: ${p.subject}`]); L.push([`Evidence attached: ${Object.keys(p.evidence || {}).length} items`]); L.push([`Submitting → ${p.target_endpoint}`]); }
      else L.push(["Checking existing complaint (no duplicate filed)"]);
      L.push([`Reference: ${o.external_reference} → ${clean(o.institution)}`]);
      L.push([`Institution: ${o.response?.message || clean(o.submission_status)}`]);
      if (o.followup) L.push([`Follow-up: ${o.followup}`]);
      L.push([`Status: ${clean(o.submission_status)}`, "final"]); break;
    }
    case "sla":
      L.push([`Applicable TAT: ${o.label || "n/a"}`]); if (o.deadline) L.push([`Deadline: ${o.deadline.replace("T", " ").slice(0, 16)}`]);
      L.push([`Elapsed ${o.elapsed_days}d${o.remaining_days != null ? " · remaining " + o.remaining_days + "d" : ""}`]);
      L.push([`Status: ${clean(o.status)}`]);
      if (o.compensation_status && o.compensation_status !== "NOT_APPLICABLE") L.push([`Potential compensation: ${inr(o.potential_compensation_inr)} (${clean(o.compensation_status)})`]);
      if (o.escalation?.reason) L.push([o.escalation.reason]);
      L.push([o.next_action || "continue monitoring", "final"]); break;
    case "comms":
      L.push(["Drafting customer update"]); L.push([`"${(o.customer || "").slice(0, 80)}…"`]);
      if (o.merchant) L.push(["Merchant notice (no customer data)"]);
      L.push([`Sent (simulated): ${(o.customer ? "customer" : "")}${o.merchant ? ", merchant" : ""}`, "final"]); break;
    case "monitor": L.push(["Saving case state to the database"]); L.push(["Writing audit trail"]); L.push([`Case status → ${clean(o.status)}`, "final"]); break;
    default: L.push([t.summary || "working", "final"]);
  }
  return L;
}

async function playTrace(trace) {
  const token = ++playToken;
  if (expanded) collapse(expanded);
  FLOW.nodes.forEach(n => setNode(n.id, "skipped", "not used", "")); edgeState = {}; nodeTrace = {}; drawEdges();
  let prev = null;
  for (const t of trace) {
    if (token !== playToken) return;
    (nodeTrace[t.node] ||= []).push(t);
    setNode(t.node, "running", "", "");
    if (prev) { edgeState[prev + ">" + t.node] = "on"; drawEdges(); }
    const lines = stepsFor(t); expand(t.node, lines.length); inspect(t.node);
    for (let i = 0; i < lines.length; i++) { if (token !== playToken) return; addLine(t.node, lines[i][0], lines[i][1], i); await sleep(lines[i][1] === "final" ? 380 : 230); }
    const waiting = t.node === "human_review";
    setNode(t.node, waiting ? "waiting" : "done", t.summary, t.label);
    if (prev) { edgeState[prev + ">" + t.node] = "used"; drawEdges(); }
    await sleep(350);
    prev = t.node;
  }
}
function inspect(id) {
  const items = nodeTrace[id] || [], n = FLOW.nodes.find(x => x.id === id);
  $("#insp").innerHTML = `<h3>${esc(n.title)}</h3>` + (items.length ? items.map(t => `${badge(t.label)}<div style="margin:6px 0">${esc(t.summary)}</div><pre>${esc(JSON.stringify(t.output, null, 2))}</pre>`).join("") : `<p class="muted">No activity from this block in the last run.</p>`);
}

async function runFlow(user, txn, desc, btn) {
  const b = $(btn); b.disabled = true; const old = b.textContent; b.textContent = "Running…";
  try {
    const r = await api("/api/complaints", { user_id: user, transaction_id: txn || null, description: desc });
    currentCase = r.case_id; $("#outbox").innerHTML = ""; await playTrace(r.trace); renderCaseBar(r.state, r.case_id); renderOutput(r.state);
    TXNS = await api("/api/transactions");
    return r;
  } catch (e) { alert(e.message); } finally { b.disabled = false; b.textContent = old; }
}
function renderOutput(s) {
  const inv = s.investigation, res = s.resolution, sla = s.sla || {}, c = s.complaint, cm = s.comms, cl = x => esc(String(x ?? "-").replaceAll("_", " "));
  if (!inv || !res) { $("#outbox").innerHTML = `<div class="out"><div class="oc wide"><small>Output</small><b>${cl(s.status)}</b><p>${cl((s.router?.missing_information || []).join(", "))} — the customer was asked for more details.</p></div></div>`; return; }
  $("#outbox").innerHTML = `<div class="out">
    <div class="oc"><small>1 · What happened to the money?</small><b>${cl(inv.last_confirmed_state)}</b><p>${esc(inv.summary)}</p></div>
    <div class="oc"><small>2 · Who must act next?</small><b>${cl(res.responsible_next_party)}</b><p>${esc(res.reason)}</p></div>
    <div class="oc"><small>3 · What next, and by when?</small><b>${cl(res.recommended_action)}</b><p>${esc(sla.label || "")} ${sla.deadline ? "· deadline " + esc(sla.deadline.replace("T", " ").slice(0, 16)) : ""} ${sla.status ? chip(sla.status) : ""}</p></div>
    <div class="oc wide"><small>Message sent to customer (simulated)</small><p>${esc(cm?.customer || "")}</p>${c ? `<p class="muted">Complaint ${esc(c.external_reference)} → ${cl(c.institution)} · case ${esc(s.case_id)}</p>` : ""}
      <div class="pills"><span class="chip">confidence ${esc(inv.evidence_confidence)}</span><span class="chip">${esc(inv.kind === "MERCHANT" ? "merchant payment" : "peer transfer")}</span>${res.recommended_channel ? `<span class="chip">channel ${esc(res.recommended_channel)}</span>` : ""}${(inv.missing_evidence || []).map(m => `<span class="chip UNKNOWN">missing ${cl(m).toLowerCase()}</span>`).join("")}${sla.escalation?.level ? `<span class="chip HUMAN_REVIEW">${cl(sla.escalation.level)}</span>` : ""}${sla.potential_compensation_inr ? `<span class="chip POTENTIAL">potential compensation ${inr(sla.potential_compensation_inr)}</span>` : ""}</div></div></div>`;
}
function renderCaseBar(s, id) {
  const sla = s.sla, c = s.complaint;
  $("#casebar").innerHTML = `<b>${esc(id)}</b> ${chip(s.status)} ${s.resolution ? `<span>${esc(s.resolution.issue_category)} → ${esc(s.resolution.responsible_next_party)}</span>` : ""}
    ${c ? `<span class="muted">ref ${esc(c.external_reference)}</span>` : ""}${sla && sla.label ? `<span>${esc(sla.label)} ${chip(sla.status)} day ${sla.elapsed_days?.toFixed?.(1) ?? "-"}</span>` : ""}
    <span style="flex:1"></span>
    <button data-adv="1">⏩ +1 day</button><button data-adv="3">⏩ +3 days</button><button data-ap="1">✔ Human approve</button><button data-view="1">Open case ▸</button>`;
  $("#casebar").onclick = async e => {
    const d = e.target.dataset; if (!d.adv && !d.ap && !d.view) return;
    if (d.view) { show("case"); loadCaseList(id); return; }
    const r = await api(`/api/cases/${id}/advance`, { days: +d.adv || 0, human_approved: d.ap ? true : null });
    $("#outbox").innerHTML = ""; await playTrace(r.trace); renderCaseBar(r.state, id); renderOutput(r.state);
  };
}
let sampleI = 0;
function runSample() {
  const i = sampleI++ % 5, [, u, t, d] = SCEN[i];
  $("#f-user").value = u; fillTxns("#f-user", "#f-txn"); $("#f-txn").value = t; $("#f-desc").value = d;
  runFlow(u, t, d, "#f-sample");
}


// ---------------- new complaint ----------------
async function submitNew() {
  $("#n-result").textContent = "Running agents…";
  const idv = $("#n-id").value.trim();
  const r = await runFlowQuiet($("#n-user").value, idv || $("#n-txn").value, $("#n-desc").value);
  $("#n-result").innerHTML = `Case <b>${esc(r.case_id)}</b> created – ${chip(r.status)} <a href="#" id="n-open">open case</a> · <a href="#" id="n-flow">watch agents</a>`;
  $("#n-open").onclick = e => { e.preventDefault(); show("case"); loadCaseList(r.case_id); };
  $("#n-flow").onclick = e => { e.preventDefault(); show("flow"); playTrace(r.trace); renderCaseBar(r.state, r.case_id); };
}
async function runFlowQuiet(u, t, d) { return api("/api/complaints", { user_id: u, transaction_id: t || null, description: d }); }

// ---------------- overview ----------------
async function loadOverview() {
  const [o, cs, au, chats, devs] = await Promise.all([api("/api/overview"), api("/api/cases"), api("/api/audit?limit=7"), api("/api/wa/chats"), api("/api/devices")]);
  const card = (v, l) => `<div class="card"><div class="v">${v}</div><div class="l">${l}</div></div>`;
  $("#ov-cards").innerHTML = card(o.total, "Total disputes") + card(o.open, "Open cases") + card(o.resolved, "Resolved or closed") + card(o.pending_responses, "Awaiting institution reply") + card(o.sla_breaches, "SLA breaches") + card(o.avg_investigation_secs + "s", "Avg investigation time");
  const by = {}; cs.forEach(c => by[c.status] = (by[c.status] || 0) + 1);
  const max = Math.max(1, ...Object.values(by));
  $("#ov-status").innerHTML = Object.entries(by).sort((a, b) => b[1] - a[1]).map(([k, v]) => `<div class="b"><span>${esc(k.replaceAll("_", " ").toLowerCase())}</span><div class="bar-track"><div class="bar-fill" style="width:${v / max * 100}%"></div></div><span>${v}</span></div>`).join("") || `<p class="muted">No cases yet.</p>`;
  const live = devs.filter(d => d.status === "active").length;
  $("#ov-system").innerHTML = [["Supabase", o.supabase.ready ? `live · ${o.supabase.mirrored} rows mirrored` : "tables not created yet"], ["Neo4j graph", "connected (Aura)"],
    ["WhatsApp", `${chats.length} chats · ${live} active device${live === 1 ? "" : "s"}`], ["SLA worker", `${o.worker.ticks} checks · ${o.worker.processed} cases re-evaluated`],
    ["Policy engine", "deterministic · always on"], ["Mode", "simulated institutions"]].map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("");
  $("#ov-feed").innerHTML = au.map(x => `<div class="f"><span class="t">${esc(x.ts.slice(11, 19))}</span><span><b>${esc(x.agent_name)}</b></span><span>${esc(x.output_summary)}</span></div>`).join("") || `<p class="muted">No activity yet.</p>`;
  $("#ov-cases").innerHTML = caseTable(cs.slice(0, 8));
  bindRows();
}
function caseTable(cs) {
  if (!cs.length) return `<p class="muted">No cases yet — run a scenario in Agent Workflow.</p>`;
  return `<table><tr><th>Case</th><th>Customer</th><th>Amount</th><th>Category</th><th>Next party</th><th>SLA</th><th>Status</th></tr>` +
    cs.map(c => `<tr class="click" data-case="${c.case_id}"><td><b>${c.case_id}</b></td><td>${esc(c.customer)}</td><td>${inr(c.amount)}</td><td>${esc(c.issue_category)}</td><td>${esc(c.responsible_institution || "-")}</td><td>${esc(c.applicable_sla || "-")}</td><td>${chip(c.status)}</td></tr>`).join("") + `</table>`;
}
function bindRows() { document.querySelectorAll("tr[data-case]").forEach(r => r.onclick = () => { show("case"); loadCaseList(r.dataset.case); }); }

// ---------------- case investigation ----------------
async function loadCaseList(sel) {
  CASES = await api("/api/cases");
  $("#c-select").innerHTML = CASES.map(c => `<option value="${c.case_id}">${c.case_id} · ${inr(c.amount)} · ${c.status}</option>`).join("");
  const id = sel || $("#c-select").value || (CASES[0] && CASES[0].case_id);
  if (id) { $("#c-select").value = id; loadCase(id); } else $("#c-body").innerHTML = `<p class="muted">No cases yet.</p>`;
}
async function loadCase(id) {
  const d = await api("/api/cases/" + id), s = d.state, inv = s.investigation, res = s.resolution, pol = s.policy, t = d.transaction;
  const kv = (k, v) => `<tr><td class="muted">${k}</td><td>${v}</td></tr>`;
  $("#c-body").innerHTML = `
  <div class="cards">
    <div class="card"><div class="v">${esc(d.dispute.case_id)}</div><div class="l">${chip(d.dispute.status)}</div></div>
    <div class="card"><div class="v">${inr(t.amount)}</div><div class="l">${esc(t.transaction_id)} · UPI ${esc(t.upi_ref)}</div></div>
    <div class="card"><div class="v" style="font-size:15px">${esc(inv?.last_confirmed_state || "-")}</div><div class="l">Last confirmed financial state</div></div>
    <div class="card"><div class="v" style="font-size:15px">${esc(res?.responsible_next_party || "-")}</div><div class="l">Responsible next institution</div></div>
  </div>
  <div class="grid2" style="margin-top:12px">
    <div class="panel"><h3 style="margin-top:0">Parties</h3><table>
      ${kv("Customer", `${esc(d.customer?.name)} · ${esc(d.customer?.bank)}`)}${kv("Beneficiary", `${esc(t.beneficiary_name)} · ${esc(t.beneficiary_bank)}${t.merchant_id ? " (" + esc(t.merchant_id) + ")" : ""}`)}
      ${kv("Paytm app shows", chip(inv?.paytm_app_status || t.app_status))}${kv("Evidence confidence", esc(inv?.evidence_confidence || "-"))}
      ${kv("Missing evidence", (inv?.missing_evidence || []).map(x => `<div>• ${esc(x)}</div>`).join("") || "none")}
      ${inv?.conflicts?.length ? kv("⚠ Conflicts", inv.conflicts.map(esc).join("<br>")) : ""}
      ${kv("Customer claims", `<span class="muted">(unverified)</span> ${esc(JSON.stringify(s.router?.claims || {}))}`)}</table></div>
    <div class="panel"><h3 style="margin-top:0">Investigation summary</h3><p>${esc(inv?.summary || "-")}</p>
      <h3>Routing</h3><p>${badge("AI recommendation")}${esc(res?.reason || "-")}</p>
      <p class="muted">Rule: ${esc(res?.recommended_action || "-")} · SLA ${esc(res?.applicable_sla || "-")} (${esc(res?.sla_source || "")})</p></div>
  </div>
  <div class="grid2">
    <div class="panel"><h3 style="margin-top:0">Transaction timeline</h3><div class="timeline">${(inv?.timeline || []).map(e => `<div><b>${esc(e.source)}</b> — ${esc(e.event)} ${chip(e.status)}<br><span class="muted">${esc(e.t.replace("T", " ").slice(0, 19))}</span></div>`).join("")}</div></div>
    <div class="panel"><h3 style="margin-top:0">Evidence by institution</h3><table><tr><th>Source</th><th>Type</th><th>State</th></tr>${d.evidence.map(e => `<tr><td>${esc(e.source_institution)}</td><td>${esc(e.evidence_type)}</td><td>${chip(e.evidence_status)}</td></tr>`).join("")}</table></div>
  </div>
  <div class="grid2">
    <div class="panel"><h3 style="margin-top:0">Policy engine ${pol ? chip(pol.decision) : ""}</h3>${(pol?.checks || []).map(c => `<div>${c.passed ? "✅" : "❌"} <b>${esc(c.check)}</b> <span class="muted">${esc(c.detail)}</span></div>`).join("")}</div>
    <div class="panel"><h3 style="margin-top:0">Complaints & notifications</h3>
      ${d.complaints.map(c => `<div class="msg">${badge("Simulated API response")}<b>${esc(c.external_reference)}</b> → ${esc(c.institution)} ${chip(c.submission_status)}<br><span class="muted">${esc(c.response?.message || "")}${c.response?.followup ? "<br>↳ " + esc(c.response.followup) : ""}</span></div>`).join("") || `<p class="muted">No complaint submitted.</p>`}
      ${d.notifications.map(n => `<div class="msg"><b>${esc(n.recipient)}</b> <span class="muted">(simulated)</span><br>${esc(n.message)}</div>`).join("")}</div>
  </div>
  <div class="panel"><h3 style="margin-top:0">Neo4j knowledge graph <span class="muted" style="font-weight:400">— live from Aura</span></h3><div id="c-neo" class="muted">loading…</div></div>`;
  drawNeo(d.dispute.user_id);
}
async function drawNeo(uid) {
  const box = $("#c-neo"); if (!box) return;
  try {
    const g = await api("/api/neo4j/user/" + uid); const W2 = 900, H2 = 300, cx = W2 / 2, cy = H2 / 2;
    const col = { user: "#ffffff", txn: "#9a9a9a", party: "#6b6b6b", case: "#d6d6d6", institution: "#bdbdbd", complaint: "#808080" };
    const rings = { user: 0, case: 1, txn: 1, institution: 2, complaint: 2, party: 2 }, byR = {}; g.nodes.forEach(n => (byR[rings[n.type]] ||= []).push(n));
    const P = {}; Object.entries(byR).forEach(([r, arr]) => arr.forEach((n, i) => { const a = i / arr.length * 6.283 + r; const R = r == 0 ? 0 : 70 + r * 55; P[n.id] = { x: cx + Math.cos(a) * R * 1.9, y: cy + Math.sin(a) * R * 0.95 }; }));
    box.innerHTML = `<svg viewBox="0 0 ${W2} ${H2}" style="width:100%;background:#050505;border:1px solid #242424;border-radius:28px">${g.edges.map(e => P[e.from] && P[e.to] ? `<line x1="${P[e.from].x}" y1="${P[e.from].y}" x2="${P[e.to].x}" y2="${P[e.to].y}" stroke="#333"/>` : "").join("")}
      ${g.nodes.map(n => `<g><circle cx="${P[n.id].x}" cy="${P[n.id].y}" r="11" fill="${col[n.type]}"/><text x="${P[n.id].x}" y="${P[n.id].y + 24}" fill="#aaa" font-size="10" text-anchor="middle">${esc(String(n.label).slice(0, 22))}</text></g>`).join("")}</svg><div class="muted">${g.nodes.length} nodes · ${g.edges.length} relationships</div>`;
  } catch (e) { box.textContent = "Neo4j unavailable: " + e.message; }
}

// ---------------- complaints / sla / activity ----------------
async function loadComplaints() {
  const cs = await api("/api/cases"); const rows = [];
  cs.forEach(c => c.complaints.forEach(k => rows.push({ c, k })));
  $("#cp-body").innerHTML = rows.length ? `<table><tr><th>Reference</th><th>Case</th><th>Institution contacted</th><th>Submission</th><th>Latest response</th><th>Next action / escalation</th></tr>` +
    rows.map(({ c, k }) => `<tr class="click" data-case="${c.case_id}"><td><b>${esc(k.external_reference)}</b></td><td>${c.case_id}</td><td>${esc(k.institution)}</td><td>${chip(k.submission_status)}</td><td>${esc(k.response?.message || "")}${k.response?.followup ? "<br><span class='muted'>↳ " + esc(k.response.followup) + "</span>" : ""}</td><td>${esc(c.sla?.next_action || "-")}<br><span class="muted">${c.sla?.escalation?.level ? "Level: " + esc(c.sla.escalation.level) : "No escalation"}</span></td></tr>`).join("") + "</table>" : `<p class="muted">No complaints submitted yet.</p>`;
  bindRows();
}
async function loadSla() {
  const cs = (await api("/api/cases")).filter(c => c.sla && c.sla.label && c.sla.status !== "NOT_APPLICABLE");
  $("#sla-body").innerHTML = cs.length ? `<table><tr><th>Case</th><th>Timeline</th><th>Deadline</th><th>Progress</th><th>Elapsed / remaining</th><th>Status</th><th>Potential compensation</th></tr>` +
    cs.map(c => { const s = c.sla, pct = Math.min(100, s.elapsed_days / s.sla_days * 100); return `<tr class="click" data-case="${c.case_id}"><td><b>${c.case_id}</b><br><span class="muted">${inr(c.amount)}</span></td><td>${esc(s.label)}<br><span class="muted">${esc(s.source || "")}</span></td><td>${esc((s.deadline || "").replace("T", " ").slice(0, 16))}</td>
      <td><div class="bar-track"><div class="bar-fill ${pct >= 100 ? "bad" : ""}" style="width:${pct}%"></div></div></td><td>${s.elapsed_days}d elapsed<br>${s.remaining_days > 0 ? s.remaining_days + "d left" : "<b style='color:var(--rd)'>" + Math.abs(s.remaining_days) + "d overdue</b>"}</td><td>${chip(s.status)}</td><td>${s.compensation_status === "NOT_APPLICABLE" ? "-" : chip(s.compensation_status) + " " + inr(s.potential_compensation_inr)}</td></tr>`; }).join("") + "</table>" : `<p class="muted">No SLA-tracked cases yet.</p>`;
  bindRows();
}
async function loadActivity() {
  const a = await api("/api/audit");
  $("#act-body").innerHTML = `<table><tr><th>Time</th><th>Case</th><th>Agent</th><th>Label</th><th>What it did</th></tr>` +
    a.slice(0, 50).map(x => `<tr><td class="muted">${esc(x.ts.slice(11, 19))}</td><td>${esc(x.case_id)}</td><td><b>${esc(x.agent_name)}</b></td><td>${badge(x.label)}</td><td>${esc(x.output_summary)}</td></tr>`).join("") + "</table>";
}

// ---------------- whatsapp ----------------
let waChat = null, waTimer = null;
async function loadWa() {
  $("#wa-base").textContent = location.origin;
  const chats = await api("/api/wa/chats");
  $("#wa-chats").innerHTML = chats.length ? chats.map(c => `<div class="msg" style="cursor:pointer;${c.chat_key === waChat ? "border-color:var(--tx)" : ""}" data-k="${esc(c.chat_key)}">
      <b>${esc(c.name)}</b> <span class="muted">${esc(c.phone || "")}</span><br><span class="muted">${esc((c.last_direction === "out" ? "↗ " : "") + (c.last_message || ""))}</span><br>
      ${c.user_name ? `<span class="chip s-ok">${esc(c.user_name)}</span>` : `<span class="chip PENDING">unlinked</span>`}${c.cases.map(k => ` <span class="chip ${k.status}">${k.case_id}</span>`).join("")}${c.pending ? ` <span class="chip HUMAN_REVIEW">${c.pending} pending</span>` : ""}</div>`).join("")
    : `<p class="muted" style="padding:8px">No chats yet. Pair the extension or use the simulator.</p>`;
  document.querySelectorAll("#wa-chats [data-k]").forEach(d => d.onclick = () => { waChat = d.dataset.k; loadWa(); });
  if (!waChat && chats[0]) waChat = chats[0].chat_key;
  const c = chats.find(x => x.chat_key === waChat);
  if (c) {
    $("#wa-head").innerHTML = `<b>${esc(c.name)}</b> ${esc(c.phone || "")} &nbsp; Link to Paytm customer: <select id="wa-link"><option value="">— none —</option>${USERS.map(u => `<option value="${u.user_id}" ${u.user_id === c.user_id ? "selected" : ""}>${esc(u.name)}</option>`).join("")}</select>
      ${c.cases.map(k => `<a href="#" data-case="${k.case_id}">${k.case_id}</a>`).join(" ")}`;
    $("#wa-link").onchange = async e => { await api("/api/wa/link", { chat_key: c.chat_key, user_id: e.target.value || null }); loadWa(); };
    document.querySelectorAll("#wa-head a[data-case]").forEach(a => a.onclick = e => { e.preventDefault(); show("case"); loadCaseList(a.dataset.case); });
    const ms = await api("/api/wa/chats/" + encodeURIComponent(c.chat_key) + "/messages");
    $("#wa-msgs").innerHTML = ms.map(m => `<div style="display:flex;justify-content:${m.direction === "in" ? "flex-start" : "flex-end"};margin:6px 0"><div class="msg" style="max-width:75%;border-color:${m.direction === "in" ? "var(--line)" : "var(--tx)"}">
      <span class="muted" style="font-size:11px">${esc(m.direction === "in" ? "customer" : m.sender)} ${m.case_id ? "· " + esc(m.case_id) : ""}</span><br>${esc(m.text)}</div></div>`).join("");
    const box = $("#wa-msgs"); box.scrollTop = box.scrollHeight;
  }
  const devs = await api("/api/devices");
  $("#wa-devs").innerHTML = devs.map(d => `<div class="msg"><b>${esc(d.name)}</b> ${chip(d.status === "active" ? "CONFIRMED" : "NOT_CONFIRMED")} <span class="muted">${esc(d.prefix)} · ${d.captured || 0} msgs · seen ${esc((d.last_seen || "never").slice(11, 19))}</span> ${d.status === "active" ? `<button data-rev="${d.id}" style="padding:2px 8px;font-size:11px">revoke</button>` : ""}</div>`).join("");
  document.querySelectorAll("[data-rev]").forEach(b => b.onclick = async () => { await api(`/api/devices/${b.dataset.rev}/revoke`, {}); loadWa(); });
  clearInterval(waTimer); waTimer = setInterval(() => { if ($("#tab-whatsapp").classList.contains("on")) loadWa(); else clearInterval(waTimer); }, 5000);
}
$("#wa-adddev").onclick = async () => { const r = await api("/api/devices", { name: $("#wa-devname").value || "My browser" }); $("#wa-newkey").innerHTML = `<div class="msg"><b>Copy this key now — shown once:</b><br><kbd style="word-break:break-all">${esc(r.key)}</kbd></div>`; loadWa(); };
$("#wa-ssend").onclick = async () => { $("#wa-sres").textContent = "Running agents…"; const r = await api("/api/wa/simulate", { phone: $("#wa-sphone").value, text: $("#wa-stext").value }); waChat = r.ingest.lead_id || waChat; $("#wa-sres").textContent = JSON.stringify(r.result || r.ingest); loadWa(); };
boot();
