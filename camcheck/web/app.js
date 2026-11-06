"use strict";
/* Camera Check UI - vanilla JS, no external dependencies (CSP: script-src 'self'). */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
let TOKEN = $('meta[name="cc-token"]').content;
const esc = v => (v === null || v === undefined ? "" : String(v)).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = n => (n === null || n === undefined || n === "" || isNaN(n)) ? "–" : Number(n).toLocaleString();
const icon = (id, cls = "") => `<svg class="${cls}"><use href="#i-${id}"/></svg>`;
const CHECKS = ["Shutter fires at all speeds", "Sensor clean / no scratches", "Autofocus", "Stabilization", "LCD / EVF clear",
  "Buttons & dials", "Card slots", "Ports (USB/HDMI/mic)", "Battery door / grip", "Mount & contacts", "Video records",
  "Wi-Fi / Bluetooth", "Fan quiet (cinema)", "Media bays (cinema)"];
const FLAG_ICON = { fail: "alert", warn: "alert", info: "info", ok: "check" };

const state = { evidence: [], manual: [], analysis: null, usb: null, guides: [], status: null, selected: null,
  checks: {}, settings: null, saving: false };

// ------------------------------------------------------------------ api ----
// Re-read the session from the app (it changes whenever Camera Check restarts).
async function refreshSession() {
  const r = await fetch("/", { cache: "no-store", credentials: "same-origin" });
  const m = (await r.text()).match(/name="cc-token" content="([^"]+)"/);
  if (!m) throw new Error("Could not reconnect to Camera Check.");
  TOKEN = m[1];
}
async function appReachable() {
  try { return (await fetch("/api/hello", { cache: "no-store" })).ok; } catch (e) { return false; }
}
// One place for every request: adds the token, recovers from an app restart, and turns
// browser-level failures ("Failed to fetch") into something staff can act on.
async function request(path, opts = {}, retry = true) {
  opts.headers = Object.assign({}, opts.headers || {});
  if (opts.method && opts.method !== "GET") opts.headers["X-CC-Token"] = TOKEN;
  let r;
  try { r = await fetch(path, opts); }
  catch (e) {
    if (!(await appReachable())) throw new Error("Camera Check isn't running. Reopen Camera Check, then try again - nothing you entered has been saved yet.");
    if (retry) { await refreshSession(); return request(path, opts, false); }
    throw new Error("The connection to Camera Check was interrupted. Please try again.");
  }
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json().catch(() => ({})) : await r.text();
  if (r.status === 403 && body && body.code === "session" && retry) {
    await refreshSession();
    return request(path, opts, false);
  }
  if (!r.ok) throw new Error((body && body.error) || body || r.statusText);
  return body;
}
const api = (path, opts = {}) => request(path, opts);
const postJSON = (p, data) => api(p, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data || {}) });

function toast(msg, ms = 2400, kind = "") {
  const t = $("#toast");
  t.innerHTML = (kind ? icon(kind === "ok" ? "check" : "alert") : "") + esc(msg);
  t.classList.remove("hidden"); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add("hidden"), ms);
}

// ------------------------------------------------------------- clipboard ----
async function copyRich(text, html) {
  try {
    if (html && window.ClipboardItem) {
      await navigator.clipboard.write([new ClipboardItem({
        "text/plain": new Blob([text], { type: "text/plain" }), "text/html": new Blob([html], { type: "text/html" }) })]);
    } else await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch (_) {}
    ta.remove(); return ok;
  }
}
function tableToClip(rows, title) {
  const text = rows.map(r => r.map(c => String(c ?? "").replace(/[\t\n]/g, " ")).join("\t")).join("\n");
  const html = (title ? `<p><b>${esc(title)}</b></p>` : "") + `<table border="1" cellpadding="4" style="border-collapse:collapse;font-family:Calibri,Arial;font-size:11pt">` +
    rows.map(r => `<tr><td style="background:#f3f4f6;font-weight:bold">${esc(r[0])}</td><td>${esc(r[1])}</td></tr>`).join("") + "</table>";
  return { text, html };
}
const cp = v => (v === null || v === undefined || v === "" || v === "–") ? "" :
  `<button class="cp" data-copy="${esc(v)}" title="Copy" aria-label="Copy ${esc(v)}">${icon("copy")}</button>`;
document.addEventListener("click", async e => {
  const b = e.target.closest(".cp");
  if (!b) return;
  e.stopPropagation();
  if (await copyRich(b.dataset.copy)) { b.classList.add("done"); setTimeout(() => b.classList.remove("done"), 900); toast("Copied", 1200, "ok"); }
});

// ----------------------------------------------------------------- tabs ----
function showTab(name) {
  $$(".side nav button").forEach(x => x.classList.toggle("active", x.dataset.tab === name));
  $$(".tab").forEach(t => t.classList.toggle("active", t.id === "tab-" + name));
  if (name === "records") loadRecords();
  if (name === "ratings") loadRatings();
  if (name === "settings") loadSettings();
  window.scrollTo(0, 0);
}
$$(".side nav button").forEach(b => b.addEventListener("click", () => showTab(b.dataset.tab)));
$$(".seg").forEach(b => b.addEventListener("click", () => {
  $$(".seg").forEach(x => x.classList.toggle("active", x === b));
  $$(".srcpane").forEach(p => p.classList.toggle("hidden", p.id !== "src-" + b.dataset.src));
}));

// -------------------------------------------------------------- uploads ----
async function upload(file) {
  try {
    return await request("/api/upload", { method: "POST", headers: { "X-Filename": encodeURIComponent(file.name) }, body: file });
  } catch (e) { throw new Error(`${file.name}: ${e.message}`); }
}
function wireDrop(zone, input, handler) {
  zone.addEventListener("click", () => input.click());
  zone.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  input.addEventListener("change", () => { handler([...input.files]); input.value = ""; });
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("over"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("over"));
  zone.addEventListener("drop", e => { e.preventDefault(); zone.classList.remove("over"); handler([...e.dataTransfer.files]); });
}
wireDrop($("#drop"), $("#file"), async files => {
  if (!files.length) return;
  if (!state.status?.exiftool) toast("ExifTool is not installed - see Settings", 4000, "warn");
  busy(`Reading ${files.length} file(s)…`);
  try {
    const ev = [];
    for (const f of files) ev.push(await upload(f));
    state.evidence.push(...ev); renderEvidence();
    mergeAnalysis(await postJSON("/api/analyze", { evidence: ev }));
  } catch (e) { showError(e.message); }
});
wireDrop($("#drop2"), $("#file2"), async files => {
  try { for (const f of files) state.manual.push(await upload(f)); renderEvidence(); toast(`${files.length} photo(s) attached`, 1800, "ok"); }
  catch (e) { toast(e.message, 4000, "warn"); }
});
function renderEvidence() {
  $("#evList").innerHTML = [...state.evidence.map(e => ({ ...e, k: "file" })), ...state.manual.map(e => ({ ...e, k: "manual" }))]
    .map(e => `<span class="chip">${icon(e.k === "manual" ? "camera" : "file")}${esc(e.name)}</span>`).join("");
}
const busy = msg => { $("#results").innerHTML = `<div class="card empty">${esc(msg)}</div>`; };
const showError = msg => { $("#results").innerHTML = `<div class="flag fail">${icon("alert")}<div>${esc(msg)}</div></div>`; };

// ------------------------------------------------------------ card reader --
let knownCards = null;
const gb = n => n ? (n / 1e9).toFixed(n > 1e11 ? 0 : 1) + " GB" : "";
async function pollCards() {
  let cards;
  try { cards = await api("/api/cards"); } catch (e) { return; }
  const fresh = knownCards === null ? [] : cards.filter(c => !knownCards.includes(c.path));
  knownCards = cards.map(c => c.path);
  $("#cards").innerHTML = cards.length ? cards.map(c => `<div class="cardrow ${c.camera_card ? "cam" : ""} ${fresh.includes(c) ? "new" : ""}">
      <span class="ico">${icon("card")}</span>
      <div class="meta"><b>${esc(c.label || "(no label)")} <small>${esc(c.path)}</small></b>
        <small>${esc([gb(c.size), c.fs].filter(Boolean).join(" · "))}${c.size || c.fs ? " · " : ""}${c.layouts.length ? esc(c.layouts.join(", ")) : "no camera folders"}</small></div>
      <button data-scan="${esc(c.path)}" class="${c.camera_card ? "primary" : "secondary"}">Scan</button></div>`).join("")
    : `<div class="empty sm">No card detected. Insert a card, or connect a camera in Mass Storage mode.</div>`;
  $$("[data-scan]").forEach(b => b.addEventListener("click", () => scanPath(b.dataset.scan, b)));
  const auto = fresh.find(c => c.camera_card);
  if (auto && $("#autoScan").checked && $("#tab-evaluate").classList.contains("active")) {
    toast("Card detected: " + (auto.label || auto.path), 2000, "ok"); scanPath(auto.path);
  }
}
async function scanPath(path, btn) {
  if (btn) btn.disabled = true;
  busy(`Reading newest files on ${path}…`);
  try { state.analysis = null; state.selected = null; mergeAnalysis(await postJSON("/api/scan", { path })); }
  catch (e) { showError(e.message); }
  if (btn) btn.disabled = false;
}
$("#scanBtn").addEventListener("click", () => {
  const p = $("#scanPath").value.trim();
  if (!p) return toast("Enter a folder path", 2000, "warn");
  scanPath(p, $("#scanBtn"));
});
$("#scanPath").addEventListener("keydown", e => { if (e.key === "Enter") $("#scanBtn").click(); });

// ------------------------------------------------------------------ USB ----
$("#usbBtn").addEventListener("click", async () => {
  const b = $("#usbBtn"); b.disabled = true;
  $("#usbHint").textContent = "Talking to the camera… downloading the newest photo can take a few seconds.";
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
  finally { b.disabled = false; }
});

// ------------------------------------------------------------- analysis ----
function mergeAnalysis(res) {
  if (!state.analysis) state.analysis = { results: [], cross_flags: [] };
  state.analysis.results.push(...res.results);
  state.analysis.cross_flags = state.analysis.results.length > res.results.length ? crossCheck(state.analysis.results) : res.cross_flags;
  state.selected = null;
  render();
  $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
}
function crossCheck(rs) {
  const f = [], ok = rs.filter(r => r.model && !r.error);
  const models = [...new Set(ok.map(r => r.model))], serials = [...new Set(ok.filter(r => r.serial).map(r => r.serial))];
  if (models.length > 1) f.push({ level: "info", msg: "Results come from different camera models: " + models.join(", ") + ". Select the one being evaluated." });
  if (serials.length > 1) f.push({ level: "info", msg: "Results carry different serial numbers: " + serials.join(", ") + "." });
  return f;
}
const normSerial = s => String(s || "").toUpperCase().replace(/[^0-9A-Z]/g, "");
function serialMatch(found) {
  const exp = normSerial($("#f-label").value), f = normSerial(found);
  if (!exp || !f) return null;
  return f === exp || f.endsWith(exp) || exp.endsWith(f);
}
const goodResults = () => (state.analysis?.results || []).filter(r => !r.error);
function bestIndex() {
  const rs = goodResults();
  if (!rs.length) return -1;
  if (state.selected !== null && rs[state.selected]) return state.selected;
  let idx = rs.findIndex(r => serialMatch(r.serial) === true);
  if (idx >= 0) return idx;
  let best = -1, key = "";
  rs.forEach((r, i) => { const k = (r.primary ? "1" : "0") + (r.captured_at || ""); if (k > key) { key = k; best = i; } });
  return best >= 0 ? best : 0;
}
const bestResult = () => goodResults()[bestIndex()] || null;
const letterOf = g => (String(g || "").match(/^([A-F])\b/) || [])[1] || "N";

function gauge(leftPct, letter, center, sub) {
  const r = 84, c = 2 * Math.PI * r, frac = leftPct === null || leftPct === undefined ? 0 : Math.max(0, Math.min(1, leftPct / 100));
  const color = getComputedStyle(document.documentElement).getPropertyValue("--g" + letter).trim() || "#9ca3af";
  return `<div class="gauge"><svg viewBox="0 0 200 200"><circle class="track" cx="100" cy="100" r="${r}"/>
    <circle class="val" cx="100" cy="100" r="${r}" stroke="${color}" stroke-dasharray="${(c * frac).toFixed(1)} ${c.toFixed(1)}"/></svg>
    <div class="c"><div><b>${center}</b><span>${sub}</span></div></div></div>`;
}
function resultSummaryRows(r) {
  const rows = [["Camera", nameOf(r)], ["Model code", r.model], ["Serial", r.serial || "–"]];
  if (r.primary) rows.push([r.primary.label, fmt(r.primary.value)]);
  r.counters.filter(c => c !== r.primary && c.tag !== r.primary?.tag).forEach(c => rows.push([c.label, fmt(c.value)]));
  if (r.rating?.rated) rows.push(["Rated shutter life", fmt(r.rating.rated)]);
  if (r.life_used_pct !== null && r.life_used_pct !== undefined) rows.push(["Life used", r.life_used_pct + "%"]);
  if (r.grade) rows.push(["Shutter grade", r.grade]);
  if (r.firmware) rows.push(["Firmware", r.firmware]);
  if (r.lens) rows.push(["Lens", r.lens + (r.lens_serial ? " (s/n " + r.lens_serial + ")" : "")]);
  if (r.captured_at) rows.push(["Captured", r.captured_at]);
  rows.push(["Source file", r.file]);
  return rows;
}
const displayModel = r => (r.model || "").toLowerCase().startsWith((r.brand || "").toLowerCase() + " ") ? r.model.slice(r.brand.length + 1) : (r.model || "");
const nameOf = r => r?.display_name || r?.extra?.display_name || `${r?.brand || ""} ${displayModel(r || {})}`.trim();

function render() {
  const rs = goodResults(), errs = (state.analysis?.results || []).filter(r => r.error);
  const sel = bestIndex();
  let html = "";
  if (state.analysis?.cross_flags?.length) html += `<div class="flags">${state.analysis.cross_flags.map(flagHTML).join("")}</div>`;
  rs.forEach((r, i) => { html += resultCard(r, i, i === sel, rs.length > 1); });
  errs.forEach(r => { html += `<div class="flag fail">${icon("alert")}<div>${esc(r.file)}: ${esc(r.error)}</div></div>`; });
  if (state.usb?.ok) html += usbCard(state.usb);
  $("#results").innerHTML = html ? `<div class="mt">${html}</div>` : "";
  const b = bestResult();
  const brand = b?.brand || $("#f-brand").value;
  const g = state.guides.find(x => x.brand === brand) || b?.guide;
  $("#guideBox").innerHTML = g && (!b || !b.primary || b.primary.reliability !== "high")
    ? `<div class="card guide-card"><h2>${icon("book")}How to get a definitive reading - ${esc(g.brand)}<span class="conf ${esc(g.confidence)}">${esc(g.confidence)} confidence</span></h2>${guideBody(g)}</div>` : "";
  if (b && !$("#f-firmware").value && b.firmware) $("#f-firmware").placeholder = b.firmware;
  updateSaveSummary();
}
const flagHTML = f => `<div class="flag ${esc(f.level)}">${icon(FLAG_ICON[f.level] || "info")}<div>${esc(f.msg)}</div></div>`;

function resultCard(r, i, selected, multi) {
  const p = r.primary, pct = r.life_used_pct, letter = letterOf(r.grade);
  const left = pct === null || pct === undefined ? null : Math.max(0, 100 - pct);
  const m = serialMatch(r.serial);
  const flags = [...r.flags];
  if (m === false) flags.unshift({ level: "fail", msg: `Serial in file (${r.serial}) does not match the body label (${$("#f-label").value}).` });
  if (m === true) flags.unshift({ level: "ok", msg: "Serial in the camera matches the body label." });
  let center, sub;
  if (r.rating?.electronic_only) { center = "EL"; sub = "electronic shutter"; }
  else if (left !== null) { center = Math.round(left) + "%"; sub = "shutter life left"; }
  else if (p) { center = `<span style="font-size:26px">${fmt(p.value)}</span>`; sub = esc(p.kind === "mechanical" ? "mechanical releases" : "releases"); }
  else { center = "–"; sub = "no counter in file"; }
  const matchBadge = m === null ? "" : m ? `<span class="badge ok">${icon("check")}matches label</span>` : `<span class="badge fail">${icon("alert")}does not match label</span>`;
  const others = r.counters.map(c => `<tr><td>${esc(c.label)}</td><td class="n">${fmt(c.value)}</td><td>${cp(c.value)}</td><td><small>${esc(c.reliability)} · ${esc(c.note)}</small></td></tr>`).join("");
  return `<div class="card ${selected && multi ? "result-sel" : ""}" data-ri="${i}">
    <div class="rhead"><h2>${esc(nameOf(r))}</h2>${matchBadge}
      ${r.files_from_body > 1 ? `<span class="badge neutral">${r.files_from_body} files</span>` : ""}
      ${multi ? `<label class="inline"><input type="radio" name="sel" data-sel="${i}" ${selected ? "checked" : ""}> Use for this evaluation</label>` : ""}
      <div class="actions"><button class="secondary" data-copysum="${i}">${icon("copy")}Copy summary</button>
        <button class="secondary" data-copytab="${i}">${icon("sheet")}Copy as table</button></div></div>
    <div class="result"><div>
      ${gauge(left, letter, center, sub)}
      <div class="gauge-cap">${r.grade ? `<span class="gradechip"><i class="g${letter}">${letter}</i>${esc(r.grade.replace(/^[A-F] - /, ""))}</span>` :
        (p && !r.rating?.rated ? `<small class="muted">No rated life on file for this model.<br>Add one under Shutter ratings.</small>` : "")}
        ${pct !== null && pct !== undefined ? `<div class="muted small mt0">${fmt(p?.value)} of ${fmt(r.rating.rated)} rated · ${pct}% used</div>` : ""}</div>
    </div><div>
      <dl class="kv">
        <dt>Serial</dt><dd>${esc(r.serial || "–")} ${cp(r.serial)}</dd>
        ${r.internal_serial ? `<dt>Internal serial</dt><dd>${esc(r.internal_serial)} ${cp(r.internal_serial)}</dd>` : ""}
        <dt>Model</dt><dd>${esc(r.model || "–")} ${cp(r.model)}</dd>
        <dt>Firmware</dt><dd>${esc(r.firmware || "–")} ${cp(r.firmware)}</dd>
        <dt>Captured</dt><dd>${esc(r.captured_at || "–")}</dd>
        ${r.lens ? `<dt>Lens</dt><dd>${esc(r.lens)}${r.lens_serial ? " · s/n " + esc(r.lens_serial) : ""} ${cp(r.lens_serial || r.lens)}</dd>` : ""}
        ${r.power_on_time ? `<dt>Powered on at</dt><dd>${esc(r.power_on_time)}</dd>` : ""}
        <dt>File</dt><dd>${esc(r.file)} <small class="muted">${esc(r.file_type)}</small></dd>
      </dl>
      ${others ? `<table class="ctable">${others}</table>` : ""}
      <div class="flags">${flags.map(flagHTML).join("")}
      ${r.history?.length ? flagHTML({ level: "info", msg: "Seen before at this store: " + r.history.map(h => `${h.created_at} (${fmt(h.shutter_count)})`).join(", ") }) : ""}</div>
      <details><summary>Raw evidence tags</summary><pre>${esc(Object.entries(r.evidence_tags || {}).map(([k, v]) => k + ": " + v).join("\n"))}</pre></details>
    </div></div></div>`;
}
$("#results").addEventListener("click", async e => {
  const s = e.target.closest("[data-copysum]"), t = e.target.closest("[data-copytab]");
  const r = s ? goodResults()[+s.dataset.copysum] : t ? goodResults()[+t.dataset.copytab] : null;
  if (!r) return;
  const rows = resultSummaryRows(r);
  const ok = s ? await copyRich(rows.map(x => `${x[0]}: ${x[1]}`).join("\n")) : await copyRich(...Object.values(tableToClip(rows, nameOf(r))));
  if (ok) toast(s ? "Summary copied" : "Table copied - paste into Word or Excel", 2000, "ok");
});
$("#results").addEventListener("change", e => {
  const s = e.target.closest("[data-sel]");
  if (s) { state.selected = +s.dataset.sel; render(); }
});
function usbCard(u) {
  const m = serialMatch(u.serial), cs = Object.entries(u.counters || {});
  return `<div class="card"><div class="rhead"><h2>${icon("usb")} USB readout - ${esc(u.manufacturer || "")} ${esc(u.model || "")}</h2><span class="badge neutral">${esc(u.transport)}</span>
    ${m === null ? "" : m ? `<span class="badge ok">${icon("check")}matches label</span>` : `<span class="badge fail">${icon("alert")}does not match label</span>`}</div>
    <dl class="kv"><dt>Live counter</dt><dd><b>${u.shutter_count ? fmt(u.shutter_count) : "Not exposed over USB"}</b> ${cp(u.shutter_count)} ${cs.length ? `<small class="muted">${esc(cs[0][0])}</small>` : ""}</dd>
      <dt>Serial (USB)</dt><dd>${esc(u.serial || "–")} ${cp(u.serial)}</dd><dt>Firmware</dt><dd>${esc(u.firmware || "–")} ${cp(u.firmware)}</dd>
      ${cs.slice(1).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${fmt(v)} ${cp(v)}</dd>`).join("")}
      <dt>Newest photo</dt><dd>${u.image ? `${esc(u.image.name)} - decoded above` : esc(u.image_note || "–")}</dd></dl>
    <div class="flags">${(u.errors || []).map(e => flagHTML({ level: "warn", msg: e })).join("")}</div>
    <details><summary>USB diagnostic log</summary><pre>${esc((u.devices || []).join("\n"))}\n${esc((u.log || []).join("\n"))}</pre></details></div>`;
}

// --------------------------------------------------------------- checks ----
function renderChecks() {
  $("#checks").innerHTML = CHECKS.map(c => `<div class="chk"><span>${esc(c)}</span><span class="tri" data-k="${esc(c)}">
    ${["Pass", "Fail", "N/A"].map(v => `<button type="button" data-v="${v}" class="${state.checks[c] === v ? "on" : ""}">${v}</button>`).join("")}</span></div>`).join("");
}
$("#checks").addEventListener("click", e => {
  const b = e.target.closest(".tri button"); if (!b) return;
  const k = b.parentElement.dataset.k;
  state.checks[k] = state.checks[k] === b.dataset.v ? undefined : b.dataset.v;
  renderChecks();
});
$("#allPass").addEventListener("click", () => { CHECKS.forEach(c => { if (!/cinema/.test(c)) state.checks[c] = "Pass"; }); renderChecks(); });
$("#clearChecks").addEventListener("click", () => { state.checks = {}; renderChecks(); });

// ----------------------------------------------------------------- save ----
function collect() {
  const b = bestResult();
  const mcount = $("#f-mcount").value !== "" ? parseInt($("#f-mcount").value, 10) : null;
  let count = null, source = null, rated = null, pct = null, grade = null;
  if (b?.primary) { count = b.primary.value; source = `File: ${b.primary.label} [${b.primary.tag}] (${b.primary.reliability})`; }
  else if (state.usb?.shutter_count) { count = state.usb.shutter_count; source = `USB live counter: ${Object.keys(state.usb.counters || {})[0] || ""} (${state.usb.transport})`; }
  else if (mcount !== null && !isNaN(mcount)) { count = mcount; source = "Manual: menu/service screen"; }
  if (b?.primary && state.usb?.shutter_count) source += ` · USB live counter ${state.usb.shutter_count}`;
  if (b?.primary && mcount !== null && Math.abs(mcount - b.primary.value) > Math.max(50, b.primary.value * 0.02)) source += ` · manual reading ${mcount} differs!`;
  if (b?.rating?.rated && count) { rated = b.rating.rated; pct = Math.round(count * 1000 / rated) / 10; grade = b.grade || null; }
  if (rated && count && !grade) grade = gradeFor(pct);
  const serial = b?.serial || state.usb?.serial || null;
  const m = serialMatch(serial);
  const flags = [...(state.analysis?.cross_flags || []), ...(b?.flags || [])];
  if (m === false) flags.unshift({ level: "fail", msg: `Serial mismatch: camera reports ${serial}, label reads ${$("#f-label").value}` });
  const results = b ? [b] : [];
  return {
    status: $("#f-status").value, staff: $("#f-staff").value.trim(), reference: $("#f-reference").value.trim(), seller: $("#f-seller").value.trim(),
    brand: b?.brand || state.usb?.manufacturer || $("#f-brand").value || null,
    model: b?.model || state.usb?.model || $("#f-model").value.trim() || null,
    serial, label_serial: $("#f-label").value.trim() || null,
    serial_match: m === null ? "not compared" : m ? "MATCH" : "MISMATCH",
    shutter_count: count, count_source: source, rated_life: rated, life_used_pct: pct, grade,
    runtime_hours: $("#f-runtime").value || null, sensor_hours: $("#f-sensor").value || null,
    firmware: $("#f-firmware").value.trim() || b?.firmware || state.usb?.firmware || null,
    cosmetic: $("#f-cosmetic").value, offer: $("#f-offer").value.trim(), notes: $("#f-notes").value,
    functional: Object.entries(state.checks).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join("; "),
    flags, analysis: { results, usb: state.usb }, evidence: [...state.evidence, ...state.manual],
    extra: { display_name: b ? nameOf(b) : null, reliability: b?.primary?.reliability || null, app_version: state.status?.version },
  };
}
function gradeFor(p) {
  for (const [lim, g] of [[15, "A - Excellent"], [35, "B - Very good"], [60, "C - Good"], [85, "D - Fair, priced for wear"], [100, "E - Near rated life"]]) if (p < lim) return g;
  return "F - Beyond rated life";
}
function updateSaveSummary() {
  const b = bestResult();
  const name = b ? nameOf(b) : ($("#f-brand").value ? `${$("#f-brand").value} ${$("#f-model").value}` : "");
  const m = serialMatch(b?.serial || state.usb?.serial);
  $("#saveSummary").innerHTML = name ? `Saving <b>${esc(name)}</b>${b?.primary ? ` · ${fmt(b.primary.value)} actuations` : ""}${b?.grade ? ` · grade ${esc(letterOf(b.grade))}` : ""}
    ${m === false ? ` · <span class="badge fail">serial mismatch</span>` : m ? ` · <span class="badge ok">serial verified</span>` : ""}` : "Read a camera or enter a brand/model to save.";
}
["#f-label", "#f-brand", "#f-model"].forEach(s => $(s).addEventListener("input", () => render()));

$("#saveBtn").addEventListener("click", async () => {
  if (state.saving) return;
  const rec = collect();
  if (!rec.model && !rec.brand) return toast("Read a camera or enter a brand/model first", 3000, "warn");
  if (rec.serial_match === "MISMATCH" && !confirm("The serial in the camera does NOT match the body label. Save anyway?")) return;
  state.saving = true; $("#saveBtn").disabled = true;
  try {
    try { localStorage.setItem("cc-staff", rec.staff); } catch (e) {}
    const { id, cert_code } = await postJSON("/api/records", rec);
    showSaved(id, cert_code, rec);
    resetForm(true);
  } catch (e) { toast(e.message, 5000, "warn"); }
  finally { state.saving = false; $("#saveBtn").disabled = false; }
});
function recordActions(id) {
  return `<button class="primary" data-open="/card/${esc(id)}">${icon("award")}Customer report card</button>
    <a class="button secondary" href="/api/records/${esc(id)}/card.docx">${icon("doc")}Report card (Word)</a>
    <button class="secondary" data-open="/report/${esc(id)}">${icon("print")}Internal report</button>
    <a class="button secondary" href="/api/records/${esc(id)}/report.docx">${icon("doc")}Internal report (Word)</a>
    <button class="secondary" data-sumcopy="${esc(id)}">${icon("copy")}Copy summary</button>`;
}
document.addEventListener("click", async e => {
  const o = e.target.closest("[data-open]");
  if (o) { window.open(o.dataset.open, "_blank", "noopener"); return; }
  const s = e.target.closest("[data-sumcopy]");
  if (s) { try { if (await copyRich(await api(`/api/records/${encodeURIComponent(s.dataset.sumcopy)}/summary`))) toast("Summary copied", 1800, "ok"); } catch (err) { toast(err.message, 3000, "warn"); } }
});
function showSaved(id, code, rec) {
  $("#modalCard").innerHTML = `<div class="successicon">${icon("check")}</div><h1>Evaluation saved</h1>
    <p class="muted">${esc(nameOf(rec))}${rec.shutter_count ? " · " + fmt(rec.shutter_count) + " actuations" : ""}</p>
    <div class="certbox"><div><small class="muted">Certificate ID</small><br><b>${esc(id)}</b> ${cp(id)}</div>
      <div style="text-align:right"><small class="muted">Verification code</small><br><code>${esc(code)}</code> ${cp(code)}</div></div>
    <div class="grid-actions">${recordActions(id)}<button class="ghost" data-modalclose>${icon("camera")}New evaluation</button></div>`;
  $("#modal").classList.remove("hidden");
}
$("#modal").addEventListener("click", e => { if (e.target.id === "modal" || e.target.closest("[data-modalclose]")) $("#modal").classList.add("hidden"); });

function resetForm(keepStaff) {
  Object.assign(state, { evidence: [], manual: [], analysis: null, usb: null, selected: null, checks: {} });
  $$("#tab-evaluate input:not([type=checkbox]):not([type=radio]), #tab-evaluate textarea").forEach(i => {
    if (!(keepStaff && i.id === "f-staff") && i.type !== "file") i.value = "";
  });
  $$("#tab-evaluate select").forEach(s => s.selectedIndex = 0);
  $("#f-firmware").placeholder = "";
  $("#usbHint").textContent = ""; renderEvidence(); renderChecks(); render(); statusRefresh();
}
$("#resetBtn").addEventListener("click", () => { if (!state.analysis || confirm("Clear this evaluation?")) resetForm(true); });

// -------------------------------------------------------------- records ----
let recTimer;
async function loadRecords() {
  const q = $("#recQ").value.trim(), st = $("#recStatus").value;
  const qs = `q=${encodeURIComponent(q)}&status=${encodeURIComponent(st)}`;
  $("#xlsxBtn").href = "/api/export.xlsx?" + qs;
  try {
    const [rows, stats] = await Promise.all([api("/api/records?" + qs), api("/api/stats")]);
    $("#kpis").innerHTML = [[fmt(stats.n || 0), "Evaluations"], [fmt(stats.last30 || 0), "Last 30 days"], [fmt(stats.purchased || 0), "Purchased"],
      [stats.avg_pct !== null && stats.avg_pct !== undefined ? Math.round(stats.avg_pct) + "%" : "–", "Avg shutter life used"],
      [fmt(stats.mismatches || 0), "Serial mismatches caught"]].map(([v, l]) => `<div class="kpi"><b>${v}</b><span>${l}</span></div>`).join("");
    $("#recBody").innerHTML = rows.map(r => {
      const pct = r.life_used_pct, L = letterOf(r.grade);
      return `<tr data-rid="${esc(r.id)}" tabindex="0">
      <td>${esc((r.created_at || "").slice(0, 16))}</td><td><b>${esc(nameOf(r))}</b></td>
      <td>${esc(r.serial || r.label_serial || "")} ${r.serial_match === "MISMATCH" ? '<span class="badge fail">mismatch</span>' : r.serial_match === "MATCH" ? '<span class="badge ok">✓</span>' : ""}</td>
      <td class="num">${fmt(r.shutter_count)}</td>
      <td>${pct !== null && pct !== undefined ? `<span class="minibar"><i class="g${L}" style="width:${Math.min(100, pct)}%"></i></span>${esc(pct)}%` : "–"}</td>
      <td>${r.grade ? `<span class="gradechip"><i class="g${L}">${L}</i></span>` : ""}</td>
      <td><span class="badge neutral">${esc(r.status)}</span></td><td>${esc(r.reference || "")}</td></tr>`;
    }).join("") || `<tr><td colspan="8" class="empty">${q || st ? "No records match." : "No evaluations yet - save one from the Evaluate tab."}</td></tr>`;
  } catch (e) { toast(e.message, 4000, "warn"); }
}
$("#recQ").addEventListener("input", () => { clearTimeout(recTimer); recTimer = setTimeout(loadRecords, 250); });
$("#recStatus").addEventListener("change", loadRecords);
$("#recBody").addEventListener("click", e => { const tr = e.target.closest("tr[data-rid]"); if (tr) openRecord(tr.dataset.rid); });
$("#recBody").addEventListener("keydown", e => { const tr = e.target.closest("tr[data-rid]"); if (tr && e.key === "Enter") openRecord(tr.dataset.rid); });

async function openRecord(id) {
  let r;
  try { r = await api("/api/records/" + encodeURIComponent(id)); } catch (e) { return toast(e.message, 3000, "warn"); }
  const L = letterOf(r.grade), left = r.life_used_pct !== null && r.life_used_pct !== undefined ? Math.max(0, 100 - r.life_used_pct) : null;
  $("#dwTitle").textContent = nameOf(r);
  $("#dwSub").textContent = `${r.id} · ${r.created_at}`;
  const kv = [["Model code", r.model], ["Serial (camera)", r.serial], ["Serial (label)", r.label_serial], ["Serial match", r.serial_match], ["Shutter count", r.shutter_count ? fmt(r.shutter_count) : null],
    ["Rated life", r.rated_life ? fmt(r.rated_life) : null], ["Life used", r.life_used_pct !== null && r.life_used_pct !== undefined ? r.life_used_pct + "%" : null],
    ["Grade", r.grade], ["Count source", r.count_source], ["Firmware", r.firmware], ["Staff", r.staff], ["Functional", r.functional],
    ["Verification code", r.cert_code]].filter(([, v]) => v);
  const statuses = (state.status?.statuses || []).map(s => `<option ${s === r.status ? "selected" : ""}>${esc(s)}</option>`).join("");
  $("#dwBody").innerHTML = `
    <div class="btnrow">${recordActions(r.id)}<button class="secondary" data-copytable>${icon("sheet")}Copy as table</button></div>
    ${left !== null ? `<div class="row" style="margin:0 0 14px"><span class="gradechip"><i class="g${L}">${L}</i>${esc((r.grade || "").replace(/^[A-F] - /, ""))}</span>
      <span class="muted">${Math.round(left)}% shutter life left</span></div>` : ""}
    <dl class="kv">${kv.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)} ${cp(v)}</dd>`).join("")}</dl>
    ${(r.flags || []).length ? `<h3>Checks</h3><div class="flags">${r.flags.map(flagHTML).join("")}</div>` : ""}
    <h3>Edit</h3>
    <div class="form">
      <label>Status<select id="d-status">${statuses}</select></label>
      <label>Offer<input id="d-offer" value="${esc(r.offer || "")}"></label>
      <label>PO / trade-in #<input id="d-reference" value="${esc(r.reference || "")}"></label>
      <label>Seller / customer<input id="d-seller" value="${esc(r.seller || "")}"></label>
      <label>Runtime hours<input id="d-runtime_hours" type="number" step="0.1" value="${esc(r.runtime_hours ?? "")}"></label>
      <label>Sensor hours<input id="d-sensor_hours" type="number" step="0.1" value="${esc(r.sensor_hours ?? "")}"></label>
      <label class="span2">Cosmetic<input id="d-cosmetic" value="${esc(r.cosmetic || "")}"></label>
      <label class="span2">Notes<textarea id="d-notes" rows="3">${esc(r.notes || "")}</textarea></label>
    </div>
    <p class="hint">Changing runtime or sensor hours issues a new verification code.</p>
    <div class="row between"><button class="ghost" id="d-del" style="color:var(--fail)">${icon("x")}Delete record</button>
      <button class="primary" id="d-save">${icon("save")}Save changes</button></div>`;
  $("#dwBody [data-copytable]").addEventListener("click", async () => {
    const rows = [["Camera", nameOf(r)], ...kv, ["Cosmetic", r.cosmetic], ["Offer", r.offer], ["PO", r.reference], ["Record ID", r.id]].filter(x => x[1]);
    if (await copyRich(...Object.values(tableToClip(rows, nameOf(r))))) toast("Table copied", 1800, "ok");
  });
  $("#d-save").addEventListener("click", async () => {
    const ch = {};
    ["status", "offer", "reference", "seller", "runtime_hours", "sensor_hours", "cosmetic", "notes"].forEach(k => ch[k] = $("#d-" + k).value);
    try { await postJSON("/api/records/" + encodeURIComponent(r.id), ch); toast("Saved", 1600, "ok"); closeDrawer(); loadRecords(); }
    catch (e) { toast(e.message, 4000, "warn"); }
  });
  $("#d-del").addEventListener("click", async () => {
    if (!confirm(`Delete record ${r.id} and its evidence files? This cannot be undone.`)) return;
    try { await api("/api/records/" + encodeURIComponent(r.id), { method: "DELETE" }); toast("Deleted", 1600, "ok"); closeDrawer(); loadRecords(); }
    catch (e) { toast(e.message, 4000, "warn"); }
  });
  $("#drawer").classList.remove("hidden"); $("#drawer").setAttribute("aria-hidden", "false");
}
function closeDrawer() { $("#drawer").classList.add("hidden"); $("#drawer").setAttribute("aria-hidden", "true"); }
$("#drawer").addEventListener("click", e => { if (e.target.closest("[data-close]")) closeDrawer(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") { closeDrawer(); $("#modal").classList.add("hidden"); } });

// --------------------------------------------------------------- verify ----
$("#verifyBtn").addEventListener("click", async () => {
  const id = $("#v-id").value.trim(), code = $("#v-code").value.trim();
  if (!id || !code) return toast("Enter both the certificate ID and the code", 2500, "warn");
  try {
    const r = await postJSON("/api/verify", { id, code });
    const rec = r.record;
    $("#verifyOut").innerHTML = r.valid
      ? `<div class="verify-ok"><h2 style="color:var(--ok)">${icon("shield")}Genuine report card</h2>
         <p>The code matches the record held by this store. Compare these values with the printed card:</p>
         <dl class="kv"><dt>Camera</dt><dd>${esc(nameOf(rec))}</dd><dt>Serial</dt><dd>${esc(rec.serial)}</dd>
         <dt>Shutter count</dt><dd>${fmt(rec.shutter_count)}</dd><dt>Life used</dt><dd>${esc(rec.life_used_pct ?? "–")}%</dd>
         <dt>Grade</dt><dd>${esc(rec.grade || "–")}</dd><dt>Runtime</dt><dd>${esc(rec.runtime_hours ?? "–")}</dd><dt>Inspected</dt><dd>${esc(rec.created_at)}</dd></dl></div>`
      : `<div class="verify-bad"><h2>${icon("alert")}Not verified</h2><p>${esc(r.reason)}</p></div>`;
  } catch (e) { toast(e.message, 4000, "warn"); }
});

// --------------------------------------------------------- guide/ratings ----
const guideBody = g => `<p><span class="lbl">File</span> ${esc(g.file)}</p><p><span class="lbl">USB</span> ${esc(g.usb)}</p><p><span class="lbl">Menu</span> ${esc(g.menu)}</p>
  ${g.notes?.length ? `<ul class="tight">${g.notes.map(n => `<li>${esc(n)}</li>`).join("")}</ul>` : ""}`;
async function loadRatings() {
  const rs = await api("/api/ratings");
  $("#ratBody").innerHTML = rs.map(r => `<tr><td>${esc(r.brand)}</td><td>${esc(r.name)}</td><td><code>${esc(r.match)}</code></td>
    <td class="num">${r.electronic_only ? "electronic only" : fmt(r.rated)}</td>
    <td><span class="conf ${r.confidence === "manufacturer" ? "high" : r.confidence === "store" ? "store" : "medium"}">${esc(r.confidence)}</span></td>
    <td>${r.confidence === "store" ? `<button class="link" data-rdel="${esc(r.match)}">remove</button>` : ""}</td></tr>`).join("");
}
$("#ratBody").addEventListener("click", async e => {
  const b = e.target.closest("[data-rdel]"); if (!b) return;
  await postJSON("/api/ratings", { match: b.dataset.rdel, delete: true }); loadRatings();
});
$("#r-add").addEventListener("click", async () => {
  const body = { brand: $("#r-brand").value, name: $("#r-name").value, match: $("#r-match").value.trim(), rated: $("#r-rated").value, electronic_only: $("#r-eo").checked };
  if (!body.match) return toast("Model match is required", 2500, "warn");
  try { await postJSON("/api/ratings", body); toast("Rating saved", 1600, "ok"); ["#r-brand", "#r-name", "#r-match", "#r-rated"].forEach(s => $(s).value = ""); loadRatings(); }
  catch (e) { toast(e.message, 4000, "warn"); }
});

// ------------------------------------------------------------- settings ----
const SET_FIELDS = ["store_name", "store_tagline", "store_address", "store_phone", "store_web", "default_staff", "report_disclaimer"];
async function loadSettings() {
  const s = state.settings = await api("/api/settings");
  SET_FIELDS.forEach(k => $("#s-" + k).value = s[k] || "");
  $("#keyHint").textContent = s.signing_key_hint;
  renderProfiles(s.usage_profiles || []);
  logoPreview(s.has_logo);
  const st = state.status || await statusRefresh();
  $("#dataBox").innerHTML = `<dt>Data folder</dt><dd>${esc(st.data_dir)} ${cp(st.data_dir)}</dd><dt>Version</dt><dd>${esc(st.version)}</dd>`;
  $("#toolsBox").innerHTML = `<dt>ExifTool</dt><dd>${st.exiftool ? `<span class="badge ok">v${esc(st.exiftool)}</span>` : `<span class="badge fail">not found</span>`}</dd>
    <dt>USB access</dt><dd>${st.usb.ok ? `<span class="badge ok">${esc(st.usb.name)}</span>` : `<span class="badge warn">unavailable</span> <small>${esc(st.usb.error)}</small>`}</dd>
    <dt>Platform</dt><dd>${esc(st.platform)}</dd>`;
}
function logoPreview(has) {
  $("#logoPrev").classList.toggle("hidden", !has); $("#logoDel").classList.toggle("hidden", !has);
  if (has) $("#logoPrev").src = "/logo.png?" + Date.now();
}
function renderProfiles(list) {
  $("#profiles").innerHTML = list.map(([n, v]) => `<div class="profile"><input value="${esc(n)}" data-pn><input type="number" min="1" value="${esc(v)}" data-pv>
    <button class="icon" data-pdel aria-label="Remove">${icon("x")}</button></div>`).join("");
}
$("#profiles").addEventListener("click", e => { if (e.target.closest("[data-pdel]")) e.target.closest(".profile").remove(); });
$("#addProfile").addEventListener("click", () => { $("#profiles").insertAdjacentHTML("beforeend", `<div class="profile"><input data-pn placeholder="Profile"><input type="number" min="1" data-pv placeholder="Frames / year"><button class="icon" data-pdel>${icon("x")}</button></div>`); });
$("#setSave").addEventListener("click", async () => {
  const body = {};
  SET_FIELDS.forEach(k => body[k] = $("#s-" + k).value);
  body.usage_profiles = $$("#profiles .profile").map(p => [$("[data-pn]", p).value.trim(), parseInt($("[data-pv]", p).value, 10)]).filter(([n, v]) => n && v > 0);
  try { await postJSON("/api/settings", body); toast("Settings saved", 1600, "ok"); applyStore(body.store_name); }
  catch (e) { toast(e.message, 4000, "warn"); }
});
$("#logoBtn").addEventListener("click", () => $("#logoFile").click());
$("#logoFile").addEventListener("change", async () => {
  const f = $("#logoFile").files[0]; if (!f) return;
  try { const s = await api("/api/logo", { method: "POST", body: f }); logoPreview(s.has_logo); toast("Logo updated", 1600, "ok"); }
  catch (e) { toast(e.message, 4000, "warn"); }
  $("#logoFile").value = "";
});
$("#logoDel").addEventListener("click", async () => { const s = await api("/api/logo", { method: "DELETE" }); logoPreview(s.has_logo); });
$("#keyReveal").addEventListener("click", async () => {
  if (!confirm("The signing key lets anyone create valid verification codes. Copy it only to another store PC. Continue?")) return;
  const r = await postJSON("/api/settings/reveal-key", {});
  if (await copyRich(r.signing_key)) toast("Signing key copied", 2000, "ok");
});
$("#keySet").addEventListener("click", async () => {
  const k = $("#keyNew").value.trim(); if (!k) return;
  if (!confirm("Replace this PC's signing key? Report cards issued here earlier will no longer verify unless they used the same key.")) return;
  try { const s = await postJSON("/api/settings", { signing_key: k }); $("#keyNew").value = ""; $("#keyHint").textContent = (await api("/api/settings")).signing_key_hint; toast("Signing key replaced", 2000, "ok"); }
  catch (e) { toast(e.message, 4000, "warn"); }
});
$("#recheck").addEventListener("click", async () => { await postJSON("/api/exiftool/recheck", {}); state.status = null; loadSettings(); });
$("#updBtn").addEventListener("click", async () => {
  $("#updOut").innerHTML = `<p class="hint">Checking…</p>`;
  try {
    const u = await api("/api/update-check");
    $("#updOut").innerHTML = u.newer ? `<div class="flag info">${icon("info")}<div>Version <b>${esc(u.latest)}</b> is available (you have ${esc(u.current)}). <a href="${esc(u.url)}" target="_blank" rel="noopener">Open download page</a></div></div>`
      : `<div class="flag ok">${icon("check")}<div>You have the latest version (${esc(u.current)}).</div></div>`;
  } catch (e) { $("#updOut").innerHTML = `<div class="flag warn">${icon("alert")}<div>Could not check for updates: ${esc(e.message)}</div></div>`; }
});
function applyStore(name) { $("#storeName").textContent = name || "Camera Check"; }

// ---------------------------------------------------------------- status ----
async function statusRefresh() {
  const s = state.status = await api("/api/status");
  $("#dotExif").className = "dot " + (s.exiftool ? "ok" : "bad"); $("#exifVer").textContent = s.exiftool ? s.exiftool : "missing";
  $("#dotUsb").className = "dot " + (s.usb.ok ? "ok" : "warn"); $("#usbName").textContent = s.usb.ok ? "ready" : "unavailable";
  $("#banner").classList.toggle("hidden", !!s.exiftool);
  $("#banner").textContent = "ExifTool was not found, so camera files cannot be read. Reinstall Camera Check or run tools/install_exiftool for your OS, then Settings → Re-check tools.";
  if (!s.usb.ok) $("#usbHint").textContent = s.usb.error;
  return s;
}

$("#quitBtn").addEventListener("click", async () => {
  if (!confirm("Quit Camera Check? Unsaved evaluation details will be lost.")) return;
  try { await postJSON("/api/quit", {}); } catch (e) {}
  document.body.innerHTML = `<div class="about card narrow" style="margin-top:80px"><img src="/static/icon.svg" class="abouticon" alt=""><h1>Camera Check has closed</h1><p class="muted">You can close this window.</p></div>`;
  setTimeout(() => window.close(), 400);
});

// ------------------------------------------------------------------ init ----
(async function init() {
  renderChecks();
  try {
    const [guides, settings] = await Promise.all([api("/api/guides"), api("/api/settings"), statusRefresh()]);
    state.guides = guides; state.settings = settings;
    applyStore(settings.store_name);
    let staff = settings.default_staff || "";
    try { staff = localStorage.getItem("cc-staff") || staff; } catch (e) {}
    $("#f-staff").value = staff;
    $("#f-brand").innerHTML = `<option value=""></option>` + guides.map(g => `<option>${esc(g.brand)}</option>`).join("");
    const opts = state.status.statuses.map(s => `<option>${esc(s)}</option>`).join("");
    $("#f-status").innerHTML = opts; $("#recStatus").innerHTML = `<option value="">All statuses</option>` + opts;
    $("#guideAll").innerHTML = guides.map(g => `<div class="card guide-card"><h2>${esc(g.brand)} ${g.kind === "cinema" ? '<span class="badge neutral">cinema</span>' : ""}
      <span class="conf ${esc(g.confidence)}">${esc(g.confidence)} confidence</span></h2>${guideBody(g)}</div>`).join("");
  } catch (e) { $("#banner").classList.remove("hidden"); $("#banner").textContent = e.message; }
  render();
  pollCards(); setInterval(pollCards, 3000);
  setInterval(() => fetch("/api/ping").catch(() => {}), 20000);
})();
