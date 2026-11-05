"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = v => (v === null || v === undefined ? "" : String(v)).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = n => (n === null || n === undefined || n === "") ? "–" : Number(n).toLocaleString();

const CHECKS = ["Shutter fires at all speeds", "Sensor clean / no scratches", "Autofocus OK", "Stabilization OK",
  "LCD / EVF clear", "All buttons & dials", "Card slots OK", "Ports (USB/HDMI/mic)", "Battery door / grip",
  "Mount & contacts", "Video records OK", "Wi-Fi / Bluetooth", "Fan quiet (cinema)", "Media bays (cinema)"];

const state = { evidence: [], manual: [], analysis: null, usb: null, guides: [], status: null };

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error(body.error || body || r.statusText);
  return body;
}
const postJSON = (p, data) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
function toast(msg, ms = 2600) { const t = $("#toast"); t.textContent = msg; t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms); }

// ---------------------------------------------------------------- tabs ----
$$("nav button").forEach(b => b.onclick = () => {
  $$("nav button").forEach(x => x.classList.toggle("active", x === b));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "records") loadRecords();
  if (b.dataset.tab === "ratings") loadRatings();
  if (b.dataset.tab === "setup") loadStatus();
});

// ------------------------------------------------------------- uploads ----
async function upload(file) {
  const r = await fetch("/api/upload", { method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file });
  if (!r.ok) throw new Error("Upload failed: " + file.name);
  return r.json();
}
function wireDrop(zone, input, handler) {
  zone.onclick = () => input.click();
  zone.onkeydown = e => { if (e.key === "Enter" || e.key === " ") input.click(); };
  input.onchange = () => { handler([...input.files]); input.value = ""; };
  zone.ondragover = e => { e.preventDefault(); zone.classList.add("over"); };
  zone.ondragleave = () => zone.classList.remove("over");
  zone.ondrop = e => { e.preventDefault(); zone.classList.remove("over"); handler([...e.dataTransfer.files]); };
}
wireDrop($("#drop"), $("#file"), async files => {
  if (!files.length) return;
  if (!state.status?.exiftool) { toast("ExifTool is not installed - see Setup tab", 4000); }
  $("#results").innerHTML = `<div class="card">Reading ${files.length} file(s)…</div>`;
  try {
    const ev = [];
    for (const f of files) ev.push(await upload(f));
    state.evidence.push(...ev);
    renderEvidence();
    const res = await postJSON("/api/analyze", { evidence: ev });
    mergeAnalysis(res);
  } catch (e) { $("#results").innerHTML = `<div class="card flag fail">${esc(e.message)}</div>`; }
});
wireDrop($("#drop2"), $("#file2"), async files => {
  for (const f of files) state.manual.push(await upload(f));
  renderEvidence();
});

function renderEvidence() {
  $("#evList").innerHTML = [...state.evidence.map(e => ({...e, k: "file"})), ...state.manual.map(e => ({...e, k: "manual"}))]
    .map((e, i) => `<span class="chip">${e.k === "manual" ? "📷 " : ""}${esc(e.name)}</span>`).join("");
}

$("#scanBtn").onclick = () => {
  const path = $("#scanPath").value.trim();
  if (!path) return toast("Enter a folder path");
  scanPath(path, $("#scanBtn"));
};

$("#usbBtn").onclick = async () => {
  $("#usbBtn").disabled = true; $("#usbHint").textContent = "Talking to camera… (downloading the newest photo can take a few seconds)";
  try {
    const r = await postJSON("/api/usb", {});
    state.usb = r;
    if (r.ok) {
      $("#usbHint").textContent = `Connected: ${r.manufacturer || ""} ${r.model || ""}`;
      if (r.image) { state.evidence.push({ id: r.image.evidence_id, name: r.image.name }); renderEvidence(); }
      if (r.analysis) { mergeAnalysis(r.analysis); return; }
    } else $("#usbHint").textContent = r.error;
    render();
  } catch (e) { $("#usbHint").textContent = e.message; }
  finally { $("#usbBtn").disabled = false; }
};

// ------------------------------------------------------------ card reader --
let knownCards = null;
const gb = n => n ? (n / 1e9).toFixed(n > 1e11 ? 0 : 1) + " GB" : "";
async function pollCards() {
  let cards;
  try { cards = await api("/api/cards"); } catch (e) { return; }
  const fresh = knownCards === null ? [] : cards.filter(c => !knownCards.includes(c.path));
  knownCards = cards.map(c => c.path);
  $("#cards").innerHTML = cards.length ? cards.map(c => `<div class="cardrow ${c.camera_card ? "cam" : ""} ${fresh.includes(c) ? "new" : ""}">
      <span class="ico">💾</span>
      <div class="meta"><b>${esc(c.label || "(no label)")} <small>${esc(c.path)}</small></b>
        <small>${esc(gb(c.size))} ${esc(c.fs)} · ${c.layouts.length ? esc(c.layouts.join(", ")) : "no camera folders"}</small></div>
      <button data-scan="${esc(c.path)}" ${c.camera_card ? "" : 'class="secondary"'}>Scan card</button></div>`).join("")
    : `<span class="hint">No card detected. Insert a card into the reader (or connect a camera in Mass Storage mode).</span>`;
  $$("[data-scan]").forEach(b => b.onclick = () => scanPath(b.dataset.scan, b));
  const auto = fresh.find(c => c.camera_card);
  if (auto && $("#autoScan").checked) { toast("Card detected: " + (auto.label || auto.path)); scanPath(auto.path); }
}
async function scanPath(path, btn) {
  if (btn) btn.disabled = true;
  $("#results").innerHTML = `<div class="card">Reading newest files on ${esc(path)}…</div>`;
  try { state.analysis = null; mergeAnalysis(await postJSON("/api/scan", { path })); }
  catch (e) { $("#results").innerHTML = `<div class="card flag fail">${esc(e.message)}</div>`; }
  if (btn) btn.disabled = false;
}

function mergeAnalysis(res) {
  if (!state.analysis) state.analysis = { results: [], cross_flags: [] };
  state.analysis.results.push(...res.results);
  state.analysis.cross_flags = res.cross_flags;
  if (state.analysis.results.length > res.results.length) {
    // re-run cross check client side across batches
    state.analysis.cross_flags = crossCheck(state.analysis.results);
  }
  render();
}
function crossCheck(rs) {
  const f = [], ok = rs.filter(r => r.model);
  const models = [...new Set(ok.map(r => r.model))], serials = [...new Set(ok.filter(r => r.serial).map(r => r.serial))];
  if (models.length > 1) f.push({ level: "fail", msg: "Files come from different camera models: " + models.join(", ") });
  if (serials.length > 1) f.push({ level: "fail", msg: "Files carry different serial numbers: " + serials.join(", ") });
  return f;
}

// ------------------------------------------------------------- results ----
const normSerial = s => String(s || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
function serialMatch(found) {
  const exp = normSerial($("#f-label").value), f = normSerial(found);
  if (!exp || !f) return null;
  return f === exp || f.endsWith(exp) || exp.endsWith(f);
}
function bestResult() {
  let rs = (state.analysis?.results || []).filter(r => !r.error);
  const matching = rs.filter(r => serialMatch(r.serial) === true);
  if (matching.length) rs = matching;
  const withCount = rs.filter(r => r.primary).sort((a, b) => (b.captured_at || "").localeCompare(a.captured_at || "") || b.primary.value - a.primary.value);
  return withCount[0] || rs[0] || null;
}

function render() {
  const rs = state.analysis?.results || [];
  let html = "";
  for (const f of state.analysis?.cross_flags || []) html += `<div class="flag ${f.level}">${esc(f.msg)}</div>`;
  for (const r of rs) html += r.error ? `<div class="card flag fail">${esc(r.file)}: ${esc(r.error)}</div>` : resultCard(r);
  if (state.usb?.ok) html += usbCard(state.usb);
  $("#results").innerHTML = html;
  const b = bestResult();
  const brand = b?.brand || $("#f-brand").value;
  const g = state.guides.find(x => x.brand === brand) || (b?.guide);
  $("#guideBox").innerHTML = g && (!b || !b.primary || b.primary.reliability !== "high")
    ? `<div class="card guide"><h2>How to get a definitive reading - ${esc(g.brand)}</h2>${guideHTML(g, false)}</div>` : "";
}

function resultCard(r) {
  const p = r.primary, pct = r.life_used_pct;
  const m = serialMatch(r.serial);
  const cls = pct >= 85 ? "high" : pct >= 50 ? "mid" : "";
  const matchBadge = m === null ? "" : m ? `<span class="badge ok">matches label</span>` : `<span class="badge fail">DOES NOT MATCH label</span>`;
  const flags = [...r.flags];
  if (m === false) flags.unshift({ level: "fail", msg: `Serial in file (${r.serial}) does not match the body label (${$("#f-label").value}).` });
  if (m === true) flags.unshift({ level: "ok", msg: "Serial in file matches the body label." });
  return `<div class="card"><div class="result">
    <div>
      <div class="big">${p ? fmt(p.value) : "—"}<small>${p ? esc(p.label) : "No lifetime counter in this file"}</small></div>
      ${pct !== null && pct !== undefined ? `<div class="meter ${cls}"><div style="width:${Math.min(pct, 100)}%"></div></div>
        <div>${pct}% of ${fmt(r.rating.rated)} rated <small class="conf ${r.rating.confidence === "manufacturer" ? "high" : "medium"}">${esc(r.rating.confidence)}</small></div>
        <div class="grade">${esc(r.grade)}</div>` : r.rating?.electronic_only ? `<div class="hint">Electronic-shutter-only body</div>` : r.primary ? `<div class="hint">No rated life on file for this model - add one in Shutter ratings.</div>` : ""}
    </div>
    <div>
      <dl class="kv">
        <dt>Camera</dt><dd><b>${esc(r.brand)}</b> ${esc(r.model.toLowerCase().startsWith(r.brand.toLowerCase()) ? r.model.slice(r.brand.length).trim() : r.model)}</dd>
        <dt>Serial</dt><dd>${esc(r.serial || "–")} ${matchBadge}</dd>
        ${r.internal_serial ? `<dt>Internal serial</dt><dd>${esc(r.internal_serial)}</dd>` : ""}
        <dt>Firmware</dt><dd>${esc(r.firmware || "–")}</dd>
        <dt>Captured</dt><dd>${esc(r.captured_at || "–")}</dd>
        ${r.lens ? `<dt>Lens</dt><dd>${esc(r.lens)} ${r.lens_serial ? "· s/n " + esc(r.lens_serial) : ""}</dd>` : ""}
        ${r.power_on_time ? `<dt>Powered on at</dt><dd>${esc(r.power_on_time)}</dd>` : ""}
        <dt>File</dt><dd>${esc(r.file)} <small>(${esc(r.file_type)})</small></dd>
      </dl>
      ${r.counters.length ? `<table class="ctable" style="margin-top:8px">${r.counters.map(c => `<tr><td>${esc(c.label)}</td><td><b>${fmt(c.value)}</b></td><td><small>${esc(c.reliability)} · ${esc(c.note)}</small></td></tr>`).join("")}</table>` : ""}
      ${flags.map(f => `<div class="flag ${f.level}">${esc(f.msg)}</div>`).join("")}
      ${r.history?.length ? `<div class="flag info">Seen before: ${r.history.map(h => `${esc(h.created_at)} (${fmt(h.shutter_count)})`).join(", ")}</div>` : ""}
      <details><summary>Raw evidence tags</summary><pre>${esc(Object.entries(r.evidence_tags).map(([k, v]) => k + ": " + v).join("\n"))}</pre></details>
    </div></div></div>`;
}
function usbCard(u) {
  const m = serialMatch(u.serial);
  const cs = Object.entries(u.counters || {});
  return `<div class="card"><h2>USB readout - ${esc(u.manufacturer || "")} ${esc(u.model || "")} <small>${esc(u.transport)}</small></h2>
    <div class="result"><div>
      <div class="big">${u.shutter_count ? fmt(u.shutter_count) : "—"}<small>${cs.length ? esc(cs[0][0]) : "No live counter exposed over USB"}</small></div>
    </div><div><dl class="kv">
      <dt>Serial (USB)</dt><dd>${esc(u.serial || "–")} ${m === null ? "" : m ? '<span class="badge ok">matches label</span>' : '<span class="badge fail">DOES NOT MATCH label</span>'}</dd>
      <dt>Firmware</dt><dd>${esc(u.firmware || "–")}</dd>
      ${cs.slice(1).map(([k, v]) => `<dt>${esc(k)}</dt><dd><b>${fmt(v)}</b></dd>`).join("")}
      <dt>Newest photo</dt><dd>${u.image ? `${esc(u.image.name)} - decoded above` : esc(u.image_note || "–")}</dd></dl>
      ${(u.errors || []).map(e => `<div class="flag warn">${esc(e)}</div>`).join("")}
      <details><summary>USB diagnostic log</summary><pre>${esc((u.devices || []).join("\n"))}
${esc((u.log || []).join("\n"))}</pre></details>
    </div></div></div>`;
}
// ---------------------------------------------------------------- save ----
function collect() {
  const b = bestResult();
  const mcount = $("#f-mcount").value ? parseInt($("#f-mcount").value, 10) : null;
  let count = null, source = null, rated = null, pct = null, grade = null;
  if (b?.primary) { count = b.primary.value; source = `File: ${b.primary.label} [${b.primary.tag}] (${b.primary.reliability})`; }
  else if (state.usb?.shutter_count) { count = state.usb.shutter_count; source = `USB live counter: ${Object.keys(state.usb.counters || {})[0] || ""} (${state.usb.transport})`; }
  else if (mcount !== null) { count = mcount; source = "Manual: menu/service screen"; }
  if (b?.rating?.rated && count) { rated = b.rating.rated; pct = Math.round(count * 1000 / rated) / 10; grade = b.grade; }
  if (b?.primary && state.usb?.shutter_count) source += ` · USB live counter ${state.usb.shutter_count}`;
  if (b?.primary && mcount !== null && Math.abs(mcount - b.primary.value) > Math.max(50, b.primary.value * 0.02))
    source += ` · manual reading ${mcount} differs!`;
  const serial = b?.serial || state.usb?.serial || null;
  const m = serialMatch(serial);
  const flags = [...(state.analysis?.cross_flags || []), ...((b?.flags) || [])];
  if (m === false) flags.unshift({ level: "fail", msg: `Serial mismatch: camera reports ${serial}, label reads ${$("#f-label").value}` });
  const funcs = $$("#checks select").map(s => s.value ? `${s.dataset.k}: ${s.value}` : null).filter(Boolean);
  return {
    staff: $("#f-staff").value, reference: $("#f-reference").value, seller: $("#f-seller").value,
    brand: b?.brand || state.usb?.manufacturer || $("#f-brand").value || null,
    model: b?.model || state.usb?.model || $("#f-model").value || null,
    serial, label_serial: $("#f-label").value || null,
    serial_match: m === null ? "not compared" : m ? "MATCH" : "MISMATCH",
    shutter_count: count, count_source: source, rated_life: rated, life_used_pct: pct, grade,
    runtime_hours: $("#f-runtime").value || null, sensor_hours: $("#f-sensor").value || null,
    cosmetic: $("#f-cosmetic").value, functional: funcs.join("; "), offer: $("#f-offer").value, notes: $("#f-notes").value,
    flags, analysis: { results: state.analysis?.results || [], usb: state.usb },
    evidence: [...state.evidence, ...state.manual],
  };
}
$("#saveBtn").onclick = async () => {
  const rec = collect();
  if (!rec.model && !rec.brand) return toast("Add a file, USB readout, or brand/model first");
  try {
    localStorage.setItem("cc-staff", rec.staff);
  } catch (e) {}
  const { id } = await postJSON("/api/records", rec);
  toast("Saved " + id);
  window.open("/report/" + id, "_blank");
  resetForm(true);
};
function resetForm(keepStaff) {
  state.evidence = []; state.manual = []; state.analysis = null; state.usb = null;
  $$("#tab-evaluate input, #tab-evaluate textarea").forEach(i => { if (!(keepStaff && i.id === "f-staff") && i.type !== "file") i.value = ""; });
  $$("#tab-evaluate select").forEach(s => s.value = "");
  renderEvidence(); render(); loadStatus();
}
$("#resetBtn").onclick = () => resetForm(true);
$("#f-label").oninput = () => render();
$("#f-brand").onchange = () => render();

// ------------------------------------------------------------- records ----
async function loadRecords() {
  const rows = await api("/api/records?q=" + encodeURIComponent($("#recQ").value));
  $("#recBody").innerHTML = rows.map(r => `<tr>
    <td>${esc(r.created_at)}</td><td>${esc(r.brand)} ${esc(r.model)}</td>
    <td>${esc(r.serial || r.label_serial)} ${r.serial_match === "MISMATCH" ? '<span class="badge fail">mismatch</span>' : ""}</td>
    <td>${fmt(r.shutter_count)}</td><td>${r.life_used_pct !== null ? esc(r.life_used_pct) + "%" : "–"}</td><td>${esc(r.grade || "")}</td>
    <td>${esc(r.runtime_hours || "")}</td><td>${esc(r.reference)}</td>
    <td><a href="/report/${esc(r.id)}" target="_blank">Report</a> · <a href="#" data-del="${esc(r.id)}">Delete</a></td></tr>`).join("")
    || `<tr><td colspan=9 class="hint">No evaluations saved yet.</td></tr>`;
  $$("[data-del]").forEach(a => a.onclick = async e => {
    e.preventDefault();
    if (!confirm("Delete this record and its evidence files?")) return;
    await api("/api/records/" + a.dataset.del, { method: "DELETE" }); loadRecords();
  });
}
$("#recSearch").onclick = loadRecords;
$("#recQ").onkeydown = e => { if (e.key === "Enter") loadRecords(); };

// ------------------------------------------------------------- ratings ----
async function loadRatings() {
  const rs = await api("/api/ratings");
  $("#ratBody").innerHTML = rs.map(r => `<tr><td>${esc(r.brand)}</td><td>${esc(r.name)}</td><td><code>${esc(r.match)}</code></td>
    <td>${r.electronic_only ? "electronic only" : fmt(r.rated)}</td><td><span class="conf ${r.confidence === "manufacturer" ? "high" : r.confidence === "store" ? "high" : "medium"}">${esc(r.confidence)}</span></td>
    <td>${r.confidence === "store" ? `<a href="#" data-rdel="${esc(r.match)}">remove</a>` : ""}</td></tr>`).join("");
  $$("[data-rdel]").forEach(a => a.onclick = async e => { e.preventDefault(); await postJSON("/api/ratings", { match: a.dataset.rdel, delete: true }); loadRatings(); });
}
$("#r-add").onclick = async () => {
  const body = { brand: $("#r-brand").value, name: $("#r-name").value, match: $("#r-match").value, rated: $("#r-rated").value, electronic_only: $("#r-eo").checked };
  if (!body.match) return toast("Model match is required");
  try { await postJSON("/api/ratings", body); toast("Saved"); loadRatings(); } catch (e) { toast(e.message, 4000); }
};

// --------------------------------------------------------------- guide ----
function guideHTML(g, title = true) {
  return `${title ? `<h3>${esc(g.brand)} ${g.kind === "cinema" ? "<small>(cinema)</small>" : ""}<span class="conf ${esc(g.confidence)}">${esc(g.confidence)} confidence</span></h3>` : ""}
    <p><span class="lbl">File</span> ${esc(g.file)}</p><p><span class="lbl">USB</span> ${esc(g.usb)}</p><p><span class="lbl">Menu</span> ${esc(g.menu)}</p>
    ${g.notes?.length ? `<ul>${g.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`;
}

// --------------------------------------------------------------- setup ----
async function loadStatus() {
  const s = state.status = await api("/api/status");
  $("#ver").textContent = "v" + s.version;
  $("#banner").classList.toggle("hidden", !!s.exiftool);
  $("#banner").innerHTML = "ExifTool was not found, so files cannot be read. Run <b>tools/install_exiftool</b> for your OS (see Setup tab / README), then click Re-check.";
  $("#statusBox").innerHTML = `<dl class="kv">
    <dt>ExifTool</dt><dd>${s.exiftool ? `<span class="badge ok">v${esc(s.exiftool)}</span> <small>${esc(s.exiftool_cmd)}</small>` : '<span class="badge fail">not found</span>'}</dd>
    <dt>USB camera access</dt><dd>${s.usb.ok ? `<span class="badge ok">${esc(s.usb.name)}</span>` : `<span class="badge warn">unavailable</span> <small>${esc(s.usb.error)}</small>`}</dd>
    <dt>Data folder</dt><dd>${esc(s.data_dir)}</dd><dt>Platform</dt><dd>${esc(s.platform)}</dd></dl>`;
  $("#usbBackend").textContent = s.usb.ok ? "· " + s.usb.name : "";
  if (!s.usb.ok) $("#usbHint").textContent = s.usb.error;
}
$("#recheck").onclick = async () => { await postJSON("/api/exiftool/recheck", {}); loadStatus(); };

// ---------------------------------------------------------------- init ----
(async function init() {
  $("#checks").innerHTML = CHECKS.map(c => `<label><select data-k="${esc(c)}"><option value=""></option><option>Pass</option><option>Fail</option><option>N/A</option></select>${esc(c)}</label>`).join("");
  try { $("#f-staff").value = localStorage.getItem("cc-staff") || ""; } catch (e) {}
  state.guides = await api("/api/guides");
  $("#f-brand").innerHTML = `<option value=""></option>` + state.guides.map(g => `<option>${esc(g.brand)}</option>`).join("");
  $("#guideAll").innerHTML = state.guides.map(g => `<div class="guide">${guideHTML(g)}</div>`).join("");
  await loadStatus();
  pollCards(); setInterval(pollCards, 3000);
  setInterval(() => fetch("/api/ping").catch(() => {}), 20000);
})();
