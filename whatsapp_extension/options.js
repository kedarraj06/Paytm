const $ = (id) => document.getElementById(id);
const show = (text, ok) => { $("msg").textContent = text; $("msg").className = ok ? "ok" : "bad"; };

chrome.storage.local.get(["backendUrl", "deviceKey"]).then(({ backendUrl = "", deviceKey = "" }) => {
  $("url").value = backendUrl;
  $("key").value = deviceKey;
});

$("save").onclick = async () => {
  let origin;
  try { origin = new URL($("url").value.trim()).origin; } catch { return show("Enter a valid URL, e.g. http://localhost:8000", false); }
  const key = $("key").value.trim();
  if (!key) return show("Paste the device key from the dashboard.", false);

  // Backends other than localhost need an explicit browser permission (must be requested from a click).
  if (!/^https?:\/\/(localhost|127\.0\.0\.1)(:|$)/.test(origin)) {
    const granted = await chrome.permissions.request({ origins: [origin + "/*"] });
    if (!granted) return show("Permission to reach " + origin + " was not granted.", false);
  }
  await chrome.storage.local.set({ backendUrl: origin, deviceKey: key });
  show("Testing...", true);
  const res = await chrome.runtime.sendMessage({ type: "test" });
  if (res && res.ok) show("Connected as device \"" + res.device + "\".", true);
  else show("Could not connect: " + (res?.error || "no response") + (res?.error === "invalid_device_key" ? " (check the key, or it was revoked)" : ""), false);
};
