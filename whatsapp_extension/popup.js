const $ = (id) => document.getElementById(id);

async function paint() {
  const { paused = false, lastSync } = await chrome.storage.local.get(["paused", "lastSync"]);
  const res = await chrome.runtime.sendMessage({ type: "refresh" });
  if (res && res.ok) {
    $("status").innerHTML = '<span class="ok">Connected</span> as ' + res.device
      + (res.settings.capture_enabled ? "" : " (capture disabled in dashboard)");
  } else {
    $("status").innerHTML = '<span class="bad">Not connected</span> (' + (res?.error || "no response") + ")";
  }
  $("last").textContent = lastSync
    ? "Last sync " + new Date(lastSync.at).toLocaleTimeString() + ": " + lastSync.accepted + " new messages"
    : "Nothing captured yet.";
  $("toggle").textContent = paused ? "Resume capture" : "Pause capture";
}

$("toggle").onclick = async () => {
  const { paused = false } = await chrome.storage.local.get("paused");
  await chrome.storage.local.set({ paused: !paused });
  paint();
};
$("sync").onclick = async () => {
  const tabs = await chrome.tabs.query({ url: "https://web.whatsapp.com/*" });
  tabs.forEach((t) => chrome.tabs.sendMessage(t.id, { type: "scan" }).catch(() => {}));
  setTimeout(paint, 1500);
};
$("settings").onclick = () => chrome.runtime.openOptionsPage();
paint();
