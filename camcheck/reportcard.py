"""Customer report card + internal evaluation report, as HTML (print/PDF) and Word.

The customer card deliberately excludes internal data: offer, seller, PO number,
staff notes and internal fraud flags.
"""
import html
import json
import os
import re

from . import __publisher__, __version__
from .analyzer import display_name, find_rating, load_ratings
from .ooxml import Docx

GRADE_TEXT = {
    "A": "Very little shutter use - mechanically close to new.",
    "B": "Light use, with plenty of shutter life remaining.",
    "C": "Moderate use, comfortably within the expected shutter life.",
    "D": "Heavier use - past the middle of the expected shutter life. Fully functional at inspection.",
    "E": "Close to the rated shutter life. Working at inspection; a shutter service may be needed sooner.",
    "F": "Beyond the rated shutter life. Working at inspection; budget for a shutter service.",
}
GRADE_COLOR = {"A": "16A34A", "B": "22C55E", "C": "84CC16", "D": "EAB308", "E": "F97316", "F": "DC2626"}


def _letter(grade):
    m = re.match(r"\s*([A-F])\b", grade or "")
    return m.group(1) if m else None


def _checks(functional):
    out = []
    for part in (functional or "").split(";"):
        if ":" in part:
            name, res = part.rsplit(":", 1)
            out.append({"name": name.strip(), "result": res.strip()})
    return out


def build_card(rec, settings):
    count = rec.get("shutter_count")
    rated = rec.get("rated_life")
    pct = rec.get("life_used_pct")
    analysis = rec.get("analysis") if isinstance(rec.get("analysis"), dict) else {}
    results = [r for r in analysis.get("results", []) if isinstance(r, dict) and not r.get("error")]
    best = next((r for r in results if r.get("serial") == rec.get("serial")), results[0] if results else {})
    rating = best.get("rating") or {}
    if not rating.get("name") and rec.get("model"):
        found = find_rating(rec["model"], load_ratings())
        if found:
            rating = dict(found, **{k: v for k, v in rating.items() if v is not None})
    electronic_only = bool(rating.get("electronic_only"))
    remaining = max(rated - count, 0) if (count is not None and rated) else None
    left = round(max(0.0, 100.0 - pct), 1) if pct is not None else None
    letter = _letter(rec.get("grade"))
    extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
    name = extra.get("display_name") or display_name(rec.get("brand") or "", rec.get("model") or "", rating)
    low = (best.get("primary") or {}).get("reliability") == "low" or "(low)" in (rec.get("count_source") or "")
    caveat = ("This brand's counter can roll over or reset after firmware updates, so the count shown is a "
              "lower bound and the life estimate is approximate.") if low and count else ""
    usage = []
    if remaining is not None:
        for profile, per_year in settings.get("usage_profiles") or []:
            if per_year:
                usage.append({"profile": profile, "per_year": per_year, "years": round(remaining / per_year, 1)})
    src = rec.get("count_source") or ""
    flags = rec.get("flags") or []
    stale = any("days ago" in (f.get("msg") or "") for f in flags)
    edited = any("processed by software" in (f.get("msg") or "") for f in flags)
    verifs = [
        {"label": "Serial number in the camera matches the body", "ok": rec.get("serial_match") == "MATCH",
         "na": rec.get("serial_match") in (None, "", "not compared")},
        {"label": ("Count read directly from the camera" if src.startswith(("File", "USB"))
                   else "Count read from the camera's service screen" if src.startswith("Manual")
                   else "Shutter count recorded"), "ok": bool(count), "na": not count},
        {"label": "Fresh, unedited capture taken at inspection", "ok": bool(results) and not stale and not edited,
         "na": not results},
    ]
    return {
        "id": rec.get("id"), "cert_code": rec.get("cert_code"), "date": (rec.get("created_at") or "")[:10],
        "brand": rec.get("brand") or "", "model": rec.get("model") or "", "name": name, "caveat": caveat, "serial": rec.get("serial") or rec.get("label_serial") or "",
        "firmware": rec.get("firmware") or best.get("firmware") or "", "lens": best.get("lens") or "",
        "count": count, "rated": rated, "used_pct": pct, "left_pct": left, "remaining": remaining,
        "grade": rec.get("grade") or "", "letter": letter, "grade_text": GRADE_TEXT.get(letter, ""),
        "color": GRADE_COLOR.get(letter, "6B7280"), "electronic_only": electronic_only,
        "rating_source": rating.get("confidence"), "runtime_hours": rec.get("runtime_hours"),
        "sensor_hours": rec.get("sensor_hours"), "usage": usage, "checks": _checks(rec.get("functional")),
        "cosmetic": rec.get("cosmetic") or "", "verifications": verifs, "store": settings,
    }


def _fmt(n):
    return f"{n:,}" if isinstance(n, (int, float)) and n is not None else "-"


def _hours(h):
    if h in (None, ""):
        return None
    return f"{float(h):,.1f}".rstrip("0").rstrip(".") + " h"


# =================================================================== HTML ====

_BASE_CSS = """
:root{--ink:#111827;--muted:#6b7280;--line:#e5e7eb;--accent:#c8102e}
*{box-sizing:border-box}body{margin:0;background:#eef0f3;color:var(--ink);font:14px/1.5 "Segoe UI",system-ui,-apple-system,Helvetica,Arial,sans-serif}
.page{max-width:820px;margin:24px auto;background:#fff;padding:40px 48px;border-radius:14px;box-shadow:0 6px 30px rgba(0,0,0,.08)}
.toolbar{max-width:820px;margin:16px auto 0;display:flex;gap:8px;justify-content:flex-end}
.toolbar a,.toolbar button{font:inherit;font-weight:600;border:1px solid #d1d5db;background:#fff;color:var(--ink);padding:7px 14px;border-radius:8px;cursor:pointer;text-decoration:none}
.toolbar .primary{background:var(--accent);border-color:var(--accent);color:#fff}
header{display:flex;align-items:center;gap:16px;border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:22px}
header img{max-height:54px;max-width:200px}header .store{font-size:22px;font-weight:800;letter-spacing:-.3px}
header .tag{color:var(--muted);font-size:13px}header .right{margin-left:auto;text-align:right;font-size:12px;color:var(--muted)}
h1{font-size:26px;margin:0 0 2px;letter-spacing:-.4px}h2{font-size:15px;margin:26px 0 10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.sub{color:var(--muted);margin:0 0 18px}
table{border-collapse:collapse;width:100%}td,th{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--muted);font-weight:600;width:38%}
.hero{display:grid;grid-template-columns:200px 1fr;gap:28px;align-items:center;padding:20px;border:1px solid var(--line);border-radius:14px;background:#fafafa}
.ring{position:relative;width:200px;height:200px}.ring svg{width:200px;height:200px;transform:rotate(-90deg)}
.ring .c{position:absolute;inset:0;display:grid;place-items:center;text-align:center}
.ring .c b{display:block;font-size:40px;line-height:1;letter-spacing:-1px}.ring .c span{font-size:12px;color:var(--muted)}
.grade{display:inline-flex;align-items:center;gap:10px;margin-bottom:8px}
.grade .L{width:44px;height:44px;border-radius:10px;display:grid;place-items:center;color:#fff;font-weight:800;font-size:24px}
.grade .t{font-size:18px;font-weight:700}.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:12px}
.kpi{border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:#fff}.kpi b{display:block;font-size:18px}.kpi span{font-size:11.5px;color:var(--muted)}
.ok{color:#15803d;font-weight:700}.no{color:#b91c1c;font-weight:700}.na{color:var(--muted)}
.cert{display:flex;gap:16px;align-items:center;justify-content:space-between;border:1px dashed #9ca3af;border-radius:12px;padding:12px 16px;margin-top:22px;background:#fcfcfd}
.cert code{font-size:18px;font-weight:700;letter-spacing:.08em}
.fine{color:var(--muted);font-size:11.5px;margin-top:18px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:22px}
td.ok,td.no,td.na{white-space:nowrap;width:1%}
.caveat{margin-top:10px;font-size:12.5px;color:#92400e;background:#fff7e6;border:1px solid #fde3a7;border-radius:8px;padding:6px 10px}
.pill{display:inline-block;padding:1px 9px;border-radius:999px;font-size:12px;font-weight:700}
.pill.Pass{background:#dcfce7;color:#166534}.pill.Fail{background:#fee2e2;color:#991b1b}.pill.N\\/A{background:#f3f4f6;color:#4b5563}
li.fail{color:#b91c1c}li.warn{color:#92400e}li.info{color:#1e3a8a}li.ok{color:#166534}
figure{display:inline-block;margin:6px}figure img{max-width:240px;max-height:180px;border:1px solid var(--line);border-radius:6px}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;max-width:none;border-radius:0;padding:0 6px}.toolbar{display:none}
  .hero{break-inside:avoid}@page{margin:14mm}}
@media (max-width:700px){.page{padding:22px}.hero{grid-template-columns:1fr}.two{grid-template-columns:1fr}.kpis{grid-template-columns:1fr 1fr}}
"""


def _ring(pct_left, color):
    r = 86
    circ = 2 * 3.14159265 * r
    frac = 0 if pct_left is None else max(0.0, min(1.0, pct_left / 100))
    return (f'<svg viewBox="0 0 200 200" aria-hidden="true"><circle cx="100" cy="100" r="{r}" fill="none" stroke="#e5e7eb" stroke-width="18"/>'
            f'<circle cx="100" cy="100" r="{r}" fill="none" stroke="#{color}" stroke-width="18" stroke-linecap="round" '
            f'stroke-dasharray="{circ * frac:.1f} {circ:.1f}"/></svg>')


def _header(st, e, right=""):
    logo = '<img src="/logo.png" alt="">' if st.get("has_logo") else ""
    contact = " · ".join(x for x in (st.get("store_address"), st.get("store_phone"), st.get("store_web")) if x)
    return (f'<header>{logo}<div><div class="store">{e(st.get("store_name"))}</div>'
            f'<div class="tag">{e(contact)}</div></div><div class="right">{right}</div></header>')


def card_html(c, toolbar=True):
    e = lambda v: html.escape("" if v is None else str(v))
    st = c["store"]
    tb = (f'<div class="toolbar"><a href="/api/records/{e(c["id"])}/card.docx">Download Word</a>'
          '<button class="primary" id="printBtn">Print / Save PDF</button></div>') if toolbar else ""
    if c["electronic_only"]:
        center = '<b>EL</b><span>electronic shutter<br>no mechanical wear</span>'
    elif c["left_pct"] is not None:
        center = (f'<b>{c["left_pct"]:.0f}%</b><span>shutter life<br>remaining{"<br>(estimate)" if c["caveat"] else ""}</span>')
    elif c["count"]:
        center = f'<b style="font-size:28px">{_fmt(c["count"])}</b><span>actuations</span>'
    else:
        center = '<b style="font-size:24px">-</b><span>not recorded</span>'
    grade = (f'<div class="grade"><div class="L" style="background:#{c["color"]}">{e(c["letter"])}</div>'
             f'<div><div class="t">{e(re.sub(r"^[A-F] - ", "", c["grade"]))}</div></div></div>'
             f'<div>{e(c["grade_text"])}</div>') if c["letter"] else (
        '<div class="grade"><div class="t">Shutter count recorded</div></div>'
        '<div>A published shutter rating is not available for this model, so no life estimate is given.</div>'
        if c["count"] else '<div class="grade"><div class="t">Inspection report</div></div>')
    kpis = [(_fmt(c["count"]), "Shutter actuations")]
    if c["rated"]:
        kpis.append((_fmt(c["rated"]), "Rated shutter life" + (" (reported)" if c["rating_source"] == "reported" else "")))
    if c["remaining"] is not None:
        kpis.append((_fmt(c["remaining"]), "Estimated actuations left"))
    if c["runtime_hours"] not in (None, ""):
        kpis.append((_hours(c["runtime_hours"]), "Runtime / operating hours"))
    if c["sensor_hours"] not in (None, ""):
        kpis.append((_hours(c["sensor_hours"]), "Sensor hours"))
    kpi_html = "".join(f'<div class="kpi"><b>{e(v)}</b><span>{e(l)}</span></div>' for v, l in kpis[:6])
    usage = ""
    if c["usage"]:
        usage = ("<h2>How long could it last?</h2><table><tr><th>If you shoot like a…</th><th>Per year</th><th>Approx. years left</th></tr>"
                 + "".join(f'<tr><td>{e(u["profile"])}</td><td>{_fmt(u["per_year"])} frames</td><td><b>{u["years"]:g}</b> years</td></tr>'
                           for u in c["usage"]) + "</table>")
    ver = "".join(f'<tr><td>{e(v["label"])}</td><td class="{"na" if v["na"] else "ok" if v["ok"] else "no"}">'
                  f'{"Not checked" if v["na"] else "✓ Verified" if v["ok"] else "✗ Not verified"}</td></tr>' for v in c["verifications"])
    checks = "".join(f'<tr><td>{e(x["name"])}</td><td><span class="pill {e(x["result"])}">{e(x["result"])}</span></td></tr>'
                     for x in c["checks"])
    details = [("Camera", c["name"]), ("Model code", c["model"] if c["model"] and c["model"] not in c["name"] else ""), ("Serial number", c["serial"]),
               ("Firmware", c["firmware"]), ("Lens at inspection", c["lens"]), ("Inspection date", c["date"]),
               ("Cosmetic condition", c["cosmetic"])]
    det = "".join(f"<tr><th>{e(k)}</th><td>{e(v)}</td></tr>" for k, v in details if v)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Report card - {e(c["name"])} {e(c["serial"])}</title><style>{_BASE_CSS}</style></head><body>
{tb}<div class="page">{_header(st, e, f'Certificate<br><b>{e(c["id"])}</b>')}
<h1>Camera Condition Report Card</h1><p class="sub">{e(st.get("store_tagline"))} · {e(c["name"])} · s/n {e(c["serial"])}</p>
<div class="hero"><div class="ring">{_ring(c["left_pct"], c["color"])}<div class="c"><div>{center}</div></div></div>
<div>{grade}<div class="kpis">{kpi_html}</div>{('<div class="caveat">' + e(c["caveat"]) + '</div>') if c["caveat"] else ''}</div></div>
{usage}
<div class="two"><div><h2>Camera details</h2><table>{det}</table></div>
<div><h2>Verification</h2><table>{ver}</table>{('<h2>Inspection checks</h2><table>' + checks + '</table>') if checks else ''}</div></div>
<div class="cert"><div><div style="font-size:12px;color:#6b7280">Verification code - ask {e(st.get("store_name"))} to confirm this report</div>
<code>{e(c["cert_code"])}</code></div><div style="text-align:right;font-size:12px;color:#6b7280">Certificate ID<br><b style="color:#111827">{e(c["id"])}</b></div></div>
<p class="fine">{e(st.get("report_disclaimer"))}</p>
<p class="fine">Camera Check {e(__version__)} · {e(__publisher__)}</p></div>
<script src="/static/report.js"></script></body></html>"""


def internal_html(rec, settings):
    e = lambda v: html.escape("" if v is None else str(v))
    a = rec.get("analysis") or {}
    results = a.get("results", []) if isinstance(a, dict) else []
    usbr = a.get("usb") if isinstance(a, dict) else None
    rows = internal_rows(rec)
    body = "".join(f"<tr><th>{e(k)}</th><td>{e(v) if v not in (None, '') else '-'}</td></tr>" for k, v in rows)
    flags = "".join(f"<li class='{e(f.get('level'))}'><b>{e((f.get('level') or '').upper())}</b> {e(f.get('msg'))}</li>"
                    for f in rec.get("flags") or [])
    files = ""
    for r in results:
        if not isinstance(r, dict) or r.get("error"):
            continue
        cs = "".join(f"<li>{e(c['label'])}: <b>{c['value']:,}</b> <small>({e(c['tag'])}, {e(c['reliability'])})</small></li>"
                     for c in r.get("counters", []))
        files += (f"<h3>{e(r.get('file'))}</h3><p>{e(r.get('make'))} {e(r.get('model'))} · captured "
                  f"{e(r.get('captured_at'))} · firmware {e(r.get('firmware'))}</p><ul>{cs or '<li>No counters in file</li>'}</ul>")
    if usbr and usbr.get("ok"):
        cs = "".join(f"<li>{e(k)}: <b>{v:,}</b></li>" for k, v in (usbr.get("counters") or {}).items())
        files += (f"<h3>USB readout ({e(usbr.get('transport'))})</h3><p>{e(usbr.get('manufacturer'))} {e(usbr.get('model'))}"
                  f" · serial {e(usbr.get('serial'))} · firmware {e(usbr.get('firmware'))}</p>"
                  f"<ul>{cs or '<li>No live counter exposed over USB</li>'}</ul>")
    imgs = "".join(
        f"<figure><img src='/evidence/{e(ev['id'])}/{e(ev['name'])}' alt=''><figcaption>{e(ev['name'])}</figcaption></figure>"
        for ev in rec.get("evidence") or [] if re.search(r"\.(jpe?g|png|webp)$", ev.get("name", ""), re.I))
    tb = (f'<div class="toolbar"><a href="/api/records/{e(rec["id"])}/report.docx">Download Word</a>'
          f'<a href="/card/{e(rec["id"])}">Customer report card</a><button class="primary" id="printBtn">Print / Save PDF</button></div>')
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Evaluation {e(rec['id'])}</title>
<style>{_BASE_CSS}</style></head><body>{tb}<div class="page">
{_header(settings, e, f'Internal record<br><b>{e(rec["id"])}</b>')}
<h1>Used Camera Evaluation</h1><p class="sub">Internal - not for customers</p><table>{body}</table>
<h2>Checks</h2><ul>{flags or '<li class="ok">No issues flagged</li>'}</ul>
<h2>Source data</h2>{files or '<p>Manual entry only.</p>'}
{('<h2>Evidence</h2>' + imgs) if imgs else ''}
<p style="margin-top:40px">Evaluator signature: ______________________________</p>
<p class="fine">Camera Check {e(__version__)} · {e(__publisher__)}</p></div>
<script src="/static/report.js"></script></body></html>"""


def _name(rec):
    extra = rec.get("extra") if isinstance(rec.get("extra"), dict) else {}
    return extra.get("display_name") or display_name(rec.get("brand") or "", rec.get("model") or "",
                                                     find_rating(rec.get("model") or "", load_ratings()))


def internal_rows(rec):
    return [("Record ID", rec.get("id")), ("Date", rec.get("created_at")), ("Status", rec.get("status")),
            ("Staff", rec.get("staff")), ("PO / trade-in #", rec.get("reference")), ("Seller / customer", rec.get("seller")),
            ("Camera", _name(rec)), ("Model code", rec.get("model")),
            ("Serial (from camera)", rec.get("serial")), ("Serial (body label)", rec.get("label_serial")),
            ("Serial match", rec.get("serial_match")),
            ("Shutter / actuation count", _fmt(rec.get("shutter_count")) if rec.get("shutter_count") else None),
            ("Count source", rec.get("count_source")),
            ("Rated life", _fmt(rec.get("rated_life")) if rec.get("rated_life") else None),
            ("Life used", f"{rec['life_used_pct']}%" if rec.get("life_used_pct") is not None else None),
            ("Shutter grade", rec.get("grade")), ("Runtime hours", _hours(rec.get("runtime_hours"))),
            ("Sensor hours", _hours(rec.get("sensor_hours"))), ("Firmware", rec.get("firmware")),
            ("Cosmetic", rec.get("cosmetic")), ("Functional", rec.get("functional")),
            ("Offer", rec.get("offer")), ("Notes", rec.get("notes")), ("Verification code", rec.get("cert_code"))]


# =================================================================== DOCX ====

def _logo(settings, path):
    if settings.get("has_logo") and path and os.path.isfile(path):
        with open(path, "rb") as fh:
            return fh.read()
    return None


def card_docx(c, logo_path=None):
    st = c["store"]
    d = Docx(title=f"Report card {c['id']}", creator=f"{st.get('store_name')} - Camera Check")
    logo = _logo(st, logo_path)
    if logo:
        d.image(logo, 1.5)
    contact = " · ".join(x for x in (st.get("store_address"), st.get("store_phone"), st.get("store_web")) if x)
    d.para(st.get("store_name"), bold=True, size=16, color="111827", space_after=0)
    if contact:
        d.para(contact, color="6B7280", size=9)
    d.title_block("Camera Condition Report Card",
                  f"{c['name']} · s/n {c['serial']} · inspected {c['date']}")
    if c["electronic_only"]:
        big = "Electronic shutter - no mechanical shutter wear"
    elif c["left_pct"] is not None:
        big = f"{c['left_pct']:.0f}% shutter life remaining" + (" (estimate)" if c["caveat"] else "")
    elif c["count"]:
        big = f"{_fmt(c['count'])} shutter actuations"
    else:
        big = "Inspection report"
    d.para(big, bold=True, size=22, color=c["color"], space_after=40)
    if c["left_pct"] is not None and not c["electronic_only"]:
        d.bar(c["left_pct"], fill=c["color"])
    if c["letter"]:
        d.para(runs=d._run(f"Grade {c['letter']} - ", bold=True, size=13) +
               d._run(re.sub(r"^[A-F] - ", "", c["grade"]), bold=True, size=13, color=c["color"]))
        d.para(c["grade_text"])
    if c["caveat"]:
        d.para(c["caveat"], color="92400E", size=9.5, italic=True)
    rows = [["Shutter actuations", _fmt(c["count"])]]
    if c["rated"]:
        rows.append(["Rated shutter life", _fmt(c["rated"]) + (" (reported figure)" if c["rating_source"] == "reported" else "")])
    if c["remaining"] is not None:
        rows.append(["Estimated actuations left", _fmt(c["remaining"])])
    if c["runtime_hours"] not in (None, ""):
        rows.append(["Runtime / operating hours", _hours(c["runtime_hours"])])
    if c["sensor_hours"] not in (None, ""):
        rows.append(["Sensor hours", _hours(c["sensor_hours"])])
    for k, v in (("Firmware", c["firmware"]), ("Lens at inspection", c["lens"]), ("Cosmetic condition", c["cosmetic"])):
        if v:
            rows.append([k, v])
    d.heading("Summary")
    d.table(rows, widths=[3600, 6480], shade_first_col=True)
    if c["usage"]:
        d.heading("How long could it last?")
        d.table([["If you shoot like a…", "Frames per year", "Approx. years left"]] +
                [[u["profile"], _fmt(u["per_year"]), f"{u['years']:g} years"] for u in c["usage"]],
                widths=[3600, 3240, 3240], header=True)
    d.heading("Verification")
    d.table([[v["label"], ("Not checked", {"color": "6B7280"}) if v["na"] else
              ("✓ Verified", {"color": "15803D", "bold": True}) if v["ok"] else ("✗ Not verified", {"color": "B91C1C", "bold": True})]
             for v in c["verifications"]], widths=[7080, 3000])
    if c["checks"]:
        d.heading("Inspection checks")
        d.table([[x["name"], (x["result"], {"bold": True, "color": "15803D" if x["result"] == "Pass" else
                                            "B91C1C" if x["result"] == "Fail" else "6B7280"})] for x in c["checks"]],
                widths=[7080, 3000])
    d.heading("Certificate")
    d.table([["Certificate ID", c["id"]], ["Verification code", (c["cert_code"], {"bold": True, "size": 13})]],
            widths=[3600, 6480], shade_first_col=True)
    d.para(f"Ask {st.get('store_name')} to confirm this code to verify the report has not been altered.",
           color="6B7280", size=9)
    d.para(st.get("report_disclaimer"), color="6B7280", size=8.5, italic=True)
    d.footer(f"{st.get('store_name')} · Camera Check {__version__} · {__publisher__}")
    return d.build()


def internal_docx(rec, settings, logo_path=None):
    d = Docx(title=f"Evaluation {rec.get('id')}", creator=f"{settings.get('store_name')} - Camera Check")
    logo = _logo(settings, logo_path)
    if logo:
        d.image(logo, 1.3)
    d.title_block("Used Camera Evaluation", f"Internal record {rec.get('id')} · {rec.get('created_at')}")
    d.table([[k, "-" if v in (None, "") else str(v)] for k, v in internal_rows(rec)],
            widths=[3200, 6880], shade_first_col=True)
    flags = rec.get("flags") or []
    d.heading("Checks")
    if flags:
        colors = {"fail": "B91C1C", "warn": "92400E", "info": "1E3A8A", "ok": "15803D"}
        for f in flags:
            d.para(runs=d._run((f.get("level") or "").upper() + "  ", bold=True, color=colors.get(f.get("level"))) +
                   d._run(f.get("msg")))
    else:
        d.para("No issues flagged.")
    d.para("")
    d.para("Evaluator signature: ______________________________")
    d.footer(f"{settings.get('store_name')} · internal evaluation · Camera Check {__version__} · {__publisher__}")
    return d.build()


def summary_text(rec):
    """Plain-text block for pasting into email / Word / tickets."""
    lines = [_name(rec) + (f" ({rec['model']})" if rec.get("model") and rec["model"] not in _name(rec) else ""),
             f"Serial: {rec.get('serial') or rec.get('label_serial') or '-'}" +
             (f" (label match: {rec.get('serial_match')})" if rec.get("serial_match") else "")]
    if rec.get("shutter_count"):
        s = f"Shutter count: {rec['shutter_count']:,}"
        if rec.get("rated_life"):
            s += f" of {rec['rated_life']:,} rated ({rec.get('life_used_pct')}% used)"
        lines.append(s)
    if rec.get("grade"):
        lines.append(f"Shutter grade: {rec['grade']}")
    for k, label in (("runtime_hours", "Runtime"), ("sensor_hours", "Sensor hours")):
        if rec.get(k) not in (None, ""):
            lines.append(f"{label}: {_hours(rec[k])}")
    for k, label in (("firmware", "Firmware"), ("cosmetic", "Cosmetic"), ("offer", "Offer"),
                     ("reference", "PO"), ("id", "Record"), ("cert_code", "Verification code")):
        if rec.get(k):
            lines.append(f"{label}: {rec[k]}")
    return "\n".join(lines)
