#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a consolidated RSU (Remote Software Upgrade) testcase report from the
flat CSV of clustered defects/testcases.

Pipeline:
  1. Load CSV (178 primary groups / 1658 rows).
  2. Filter to RSU-only — drop RSE (Rear Seat Entertainment) functional defects.
  3. Cluster the remaining primary testcases into functional themes.
  4. For each primary testcase, attach its linked (non-primary) defects.
  5. Render a styled HTML report: testcase first, linked defects after,
     narrow readable layout, good spacing, collapsible sections.

Output: RSU_testcases_consolidated_YYYYMMDD.html
"""
import csv
import html
import re
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone

SRC = "RSU_testcases_plain_20260901_final.csv"

# --------------------------------------------------------------------------
# 1. RSE / RSU classification
# --------------------------------------------------------------------------

# Rear Seat Entertainment *functional* feature keywords — NOT about the software
# upgrade lifecycle. These describe media/display/PADI/HMI features of the RSE
# unit itself (PADI screen, video apps, audio, brightness, camera/calls, etc.).
RSE_FUNC_KW = [
    # PADI / RSE screen hardware & UI
    "padi widget", "padi can", "padi cannot", "padi can't", "padi is not fold",
    "padi cannot be folded", "padi cannot upload", "padi gaming", "padi can not",
    "padi cannot be switched", "padi display dimming", "padi cannot operate",
    "fold down rse", "verbau_display_padi", "display dimming", "brightness",
    "no brightness change", "rse_padi", "padi-01", "padi-02",
    # Media / video / audio apps on RSE
    "media widget", "media cover", "cover art", "theatre screen", "karaoke",
    "qqmusic", "iqiyi", "tencent video", "wesing", "music", "video call",
    "videocall", "video con", "camera stream", "dingtalk", "meeting invitation",
    "video play", "video widget", "sound from speaker", "join watch",
    "the mv needs", "rse ui", "rse media", "rse still shows", "rse app menu",
    "rse picture", "picture setting", "screen sharing", "rse iconic",
    "rse_exbx-02 generic job",
    # Other RSE-HMI feature terms
    "headphone", "hdmi", "display_sensor", "display setting",
    "brightlight", "tilt angle", "reset button is greyed", "volume button",
    "touchscreen", "app in the search results cannot enter", "overlap on media",
    "font of selected ratio", "rear seat entertainment", "the fond of selected",
    "ambient sensor", "headphones audio settings", "rse headphone",
    "no response after clicking", "black screen one",
    # more RSE app / launcher / game terms
    "migu game", "vod app cover", "3rd app cover", "app belt", "hdmi shown",
    "gaming interface", "gaming controller", "launcher page overlap",
    "cde launcher", "application management cannot be loaded",
    "mv needs reload", "the mv needs", "join watch", "wesing", "iqiyi",
    "enter into the gaming", "gaming interface cannot",
    "youtube", "flickering box", "full-screen", "fullscreen",
    "spotify app layout", "spotify app", "display ratio",
]

# Remote Software Upgrade *process* keywords — the OTA upgrade lifecycle,
# flashing, backend, activation, rollback, validation, etc.
RSU_PROC_KW = [
    "remote software upgrade", "rsu campaign", "rsu app", "rsu-", "rsu uf",
    "fbm-viewer", "fbm viewer", "tasklist", "checkactivation", "check activation",
    "rollback", "software upgrade", "software update", "flash", "flashing",
    "ota", "update package", "progmgr", "validation period",
    "activation precondition", "fleem", "enrollment", "provisioning",
    "ta test", "vrum", "eswb", "external error", "initiateupdate",
    "handlegroup", "requesttasklist", "download", "install", "rsu notification",
    "rsu hmi", "rsu version", "positioning partition", "bricked", "went down",
    "upgrade", "rse26 0x", "secureboot", "diagnostic job",
]


def rsu_signal(row):
    """Return (rsu_hits, rse_hits) for a row based on textual content."""
    blob = " ".join(
        row.get(k, "") for k in (
            "defect_name", "test_name", "test_overview", "preconditions",
            "execution_steps", "expected_results",
        )
    ).lower()
    rsu = sum(1 for k in RSU_PROC_KW if k in blob)
    rse = sum(1 for k in RSE_FUNC_KW if k in blob)
    return rsu, rse


def is_rsu(row):
    """A group is RSU if its upgrade-lifecycle signal dominates RSE features.

    Strong veto: if the *defect name + test name* (the identity of the defect)
    contains an unambiguous RSE-feature term, the defect is about Rear Seat
    Entertainment functionality, not the software-upgrade lifecycle — even if
    the word "RSU" appears in the overview. Reject it.
    """
    # Strong veto: if the defect identity (name, or test_name when name is
    # missing) contains an unambiguous RSE-feature term, the defect is about
    # Rear Seat Entertainment functionality, not the software-upgrade lifecycle
    # — even if the word "RSU" appears in the overview. Reject it.
    dn = row.get("defect_name", "").strip().lower()
    tn = row.get("test_name", "").strip().lower()
    name = dn if dn and "source title unavailable" not in dn else tn
    for k in RSE_FUNC_KW:
        if k in name:
            return False
    rsu, rse = rsu_signal(row)
    return rsu > 0 and (rse == 0 or rsu >= rse)


# --------------------------------------------------------------------------
# 2. Functional theme classification
# --------------------------------------------------------------------------

def classify_theme(row):
    """Classify a primary testcase into a functional theme.

    Ordering matters: specific failure-mode signals are checked BEFORE generic
    RSU-process signals (which appear in almost every overview), so that e.g. a
    flashing failure is not swallowed by the 'activation' bucket.
    """
    name = (row["defect_name"] + " " + row["test_name"]).lower()
    over = (row["test_overview"]).lower()
    # name carries more weight than overview for topic identity
    def name_hit(kws): return any(k in name for k in kws)
    def over_hit(kws): return any(k in over for k in kws)

    # --- specific failure modes first ---
    if name_hit(["dlt", "log level", "log prefix", "logging", "info prefix",
                 "verbose", "rsu logging", "rsu-agent.service",
                 "file archive", "rsu‑related logs"]):
        return ("Logging & Diagnostics",
                "DLT log formatting, log-level prefixes, diagnostic output, "
                "rsu-agent service logging")
    if name_hit(["provisioning", "enrollment", " ta ", "ta[", "ta test",
                 "progmgr", "sfa-token", "sfa token", "token distribution"]):
        return ("Provisioning & Enrollment",
                "TA provisioning, enrollment, progmgr lifecycle, SFA tokens")
    if name_hit(["rollback"]):
        return ("Rollback & Recovery",
                "Rollback handling after failed/interrupted upgrades")
    if name_hit(["flash fail", "flash fail", "flashing failed", "flashed",
                 "bricked", "went down", "goes down", "positioning partition",
                 "partition", "ecu down", "nad flash", "remount failure",
                 "userdata open files", "open file transfer"]):
        return ("Flashing & ECU Failure",
                "ECU flashing failures, bricked ECUs, partition/remount loss")
    if name_hit(["download", "a4a package", "package could not transfer",
                 "file transfer"]):
        return ("Download & Transfer",
                "Package download / open file transfer failures")
    if name_hit(["backend", "server", "connectivity", "connection",
                 "network", "timeout", "expired", "validation period",
                 "external error"]):
        return ("Backend & Connectivity",
                "Backend connectivity, timeouts, validation-period expiries")
    if name_hit(["mobile", "ipa", "android", "ios", "mybmw", "mini 5.3",
                 "app registration", "enable rsu uf", "device manager list",
                 "connected md"]):
        return ("Mobile App Integration",
                "MyBMW / mobile app & IPA integration with RSU")
    if name_hit(["notification", "banner", "toast", "popup", "pop-ups",
                 "popups", "icon lost", "notification icon", "message pop"]):
        return ("RSU Notifications & HMI",
                "RSU notifications, banners, pop-ups, status icons in HMI")
    if name_hit(["progress bar", "progress", "flicker", "status", "queue",
                 "cancel", "resume", "pause", "suspend", "interrupt",
                 "autoinstall", "enable autoinstall"]):
        return ("RSU Process & Status",
                "Progress bar, queue, cancel/resume, activation-timer handling")
    if name_hit(["restart", "reboot", "boot", "hang", "freeze", "crash",
                 "tombstone", "exit-code", "yellow screen"]):
        return ("Stability & Restart",
                "Restart / boot / hang / crash / tombstone during RSU")
    if name_hit(["secureboot", "security", "certificate", "signature",
                 "checksum", "debug key", "permission", "missing permission",
                 "exported interface"]):
        return ("Security & Integrity",
                "SecureBoot, certificates, signature/checksum, permissions")
    if name_hit(["language", "chinese", "german", "english", "translation",
                 "i18n", "localization", "mixed in"]):
        return ("Localization & Language",
                "Language/translation issues in RSU UI")
    if name_hit(["dtc ", "0x03", "nicht fahrzeugspezifischer", "smac count",
                 "cafd", "coding", "spurious wakeup", "wakeup db",
                 "onboarding setup", "setup wizard"]):
        return ("DTC & Diagnostics",
                "Diagnostic trouble codes, SMAC/CAFD coding, onboarding/wakeup "
                "data integrity after RSU")
    if name_hit(["timer conflict", "suggest timer", "safety camera",
                 "parking payment", "lbc parking", "parking view",
                 "rear camera view", "account linking", "alexa",
                 "wifi app", "game controller"]):
        return ("Post-RSU Functional Regression",
                "Feature regressions surfaced after RSU (timers, parking, "
                "accounts, app replacements)")
    # --- HMI / UI display of the RSU flow itself (icons, layout, menu) ---
    if name_hit(["hmi", "icon", "link icon", "layout", "menu", "card",
                 "backplate", "papercut", "flat menu", "ui-lib",
                 "zone detection", "speech", "wrong", "displayed",
                 "keylist", "cryptic", "page will shift", "software info page",
                 "software release button", "installed software page"]):
        return ("RSU HMI & UI Display",
                "RSU flow HMI: icons, layout, menus, page navigation, text")
    if name_hit(["version", "information page", "version info"]):
        return ("Version & Info Display",
                "RSU version-information display and navigation")
    if name_hit(["earcon", "audio cue", "audiowiedergabe", "audio"]):
        return ("Audio Cues",
                "Earcons / audio cues during RSU activity")
    # --- campaign / activation / tasklist is the *generic* RSU-flow bucket ---
    if name_hit(["campaign", "activation", "tasklist", "checkactivation",
                 "precondition", "fleem", "activation timer",
                 "does not start", "button unavailable"]) or over_hit(
            ["campaign", "checkactivation", "tasklist", "fleem"]):
        return ("Campaign & Activation",
                "RSU campaign release, activation preconditions, tasklist")
    return ("Other RSU Issues", "Miscellaneous RSU-related defects")


# --------------------------------------------------------------------------
# 3. Field rendering helpers
# --------------------------------------------------------------------------

def esc(s):
    return html.escape(s or "")


def fmt_sim(v):
    try:
        return f"{float(v):.2f}"
    except (ValueError, TypeError):
        return "—"


def severity_badge(sev):
    sev = (sev or "").strip()
    cls = {
        "Critical": "sev-critical", "Very High": "sev-vhigh",
        "High": "sev-high", "Medium": "sev-medium",
        "Low": "sev-low",
    }.get(sev, "sev-none")
    label = sev or "N/A"
    return f'<span class="badge {cls}">{esc(label)}</span>'


def priority_badge(p):
    p = (p or "").strip()
    cls = {"P0": "pri-p0", "P1": "pri-p1", "P2": "pri-p2",
           "P3": "pri-p3"}.get(p, "pri-none")
    return f'<span class="badge {cls}">{esc(p or "N/A")}</span>'


def render_multiline(text):
    """Render a free-text field: escape, preserve line breaks as <br>,
    and turn simple 'Label:' headers into bold mini-labels. Blank lines
    become a small spacer so paragraphs are visually separated."""
    if not text or not text.strip():
        return '<span class="muted">—</span>'
    out = []
    for line in esc(text).split("\n"):
        if not line.strip():
            out.append('<span class="spacer"></span>')
            continue
        m = re.match(r"^([A-Z][A-Za-z /&]{2,30}):\s*(.*)$", line)
        if m:
            out.append(f"<b>{m.group(1)}:</b> {m.group(2)}")
        else:
            out.append(line)
    return "<br>".join(out)


def render_steps(text):
    """Render execution steps / expected results as a numbered list."""
    if not text or not text.strip():
        return '<span class="muted">—</span>'
    lines = [ln for ln in text.split("\n") if ln.strip()]
    items = []
    for ln in lines:
        ln_html = esc(ln)
        m = re.match(r"^[\-\*]?\s*(\d+)[\.\)]\s*(.*)$", ln_html)
        if m:
            items.append(f"<li>{m.group(2)}</li>")
        else:
            items.append(f"<li>{ln_html}</li>")
    return "<ol>" + "".join(items) + "</ol>"


def octane_link(defect_id):
    did = (defect_id or "").strip()
    if not did:
        return ""
    return (f"https://octane-prod.bmwgroup.net/ui/?p=1002/2001"
            f"#/entity-navigation?entityType=work_item&id={esc(did)}")


# --------------------------------------------------------------------------
# 4. HTML rendering
# --------------------------------------------------------------------------

CSS = """
:root{
  --bg:#f6f7f9; --card:#ffffff; --ink:#1f2328; --muted:#6a737d;
  --line:#e1e4e8; --accent:#1164a3; --accent-bg:#eef4fb;
  --ok:#1a7f37; --bad:#cf222e;
}
*{box-sizing:border-box}
body{
  margin:0; padding:0; background:var(--bg); color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,
    "PingFang SC","Microsoft YaHei",sans-serif;
  font-size:14px; line-height:1.6;
}
.wrap{max-width:1080px; margin:0 auto; padding:32px 24px 80px;}
header.report-head{
  background:linear-gradient(135deg,#0d2a4a 0%,#1164a3 100%);
  color:#fff; padding:32px 28px; border-radius:12px; margin-bottom:28px;
  box-shadow:0 4px 18px rgba(13,42,74,.18);
}
header.report-head h1{margin:0 0 6px; font-size:24px; font-weight:700;}
header.report-head p{margin:0; opacity:.9; font-size:13px;}
.stat-row{display:flex; flex-wrap:wrap; gap:10px; margin-top:18px;}
.stat{
  background:rgba(255,255,255,.13); border:1px solid rgba(255,255,255,.22);
  border-radius:8px; padding:8px 14px;
}
.stat .n{font-size:20px; font-weight:700; display:block;}
.stat .l{font-size:11px; opacity:.85; text-transform:uppercase; letter-spacing:.5px;}
nav.toc{
  background:var(--card); border:1px solid var(--line); border-radius:10px;
  padding:14px 18px; margin-bottom:28px;
}
nav.toc h2{margin:0 0 8px; font-size:13px; text-transform:uppercase;
  color:var(--muted); letter-spacing:.5px;}
nav.toc .toc-list{display:flex; flex-wrap:wrap; gap:6px;}
nav.toc a{
  font-size:12px; color:var(--accent); text-decoration:none;
  background:var(--accent-bg); padding:3px 10px; border-radius:14px;
  border:1px solid #d6e4f2;
}
nav.toc a:hover{background:#d6e4f2;}
nav.toc a .cnt{color:var(--muted); margin-left:4px; font-size:11px;}
section.theme{margin-bottom:36px;}
.theme-head{
  display:flex; align-items:baseline; gap:10px;
  border-bottom:2px solid var(--accent); padding-bottom:6px; margin-bottom:16px;
}
.theme-head h2{margin:0; font-size:18px; color:var(--ink);}
.theme-head .sub{color:var(--muted); font-size:12.5px;}
.theme-head .cnt{margin-left:auto; background:var(--accent); color:#fff;
  font-size:12px; padding:2px 10px; border-radius:10px;}
article.tc{
  background:var(--card); border:1px solid var(--line); border-radius:10px;
  margin-bottom:20px; overflow:hidden;
  box-shadow:0 1px 3px rgba(0,0,0,.04);
}
.tc-bar{
  display:flex; align-items:center; gap:10px; flex-wrap:wrap;
  background:var(--accent-bg); padding:11px 16px; border-bottom:1px solid #d6e4f2;
}
.tc-bar .tc-title{font-weight:600; font-size:14.5px; color:var(--ink);
  flex:1 1 60%;}
.tc-bar .meta{font-size:12px; color:var(--muted);}
.tc-body{padding:14px 16px;}
.field{margin-bottom:14px;}
.field-label{
  font-size:11.5px; text-transform:uppercase; letter-spacing:.6px;
  color:var(--accent); font-weight:700; margin:0 0 5px;
}
.field-content{font-size:13.5px; color:#24292e;}
.field-content ol{margin:4px 0 4px 18px; padding:0;}
.field-content li{margin-bottom:3px;}
.field-content .spacer{display:inline-block; height:6px;}
.grid2{display:grid; grid-template-columns:1fr 1fr; gap:16px;}
@media(max-width:720px){.grid2{grid-template-columns:1fr;}}
.kv{font-size:12.5px;}
.kv .k{color:var(--muted);}
.badge{display:inline-block; font-size:11px; font-weight:600;
  padding:2px 8px; border-radius:10px; border:1px solid transparent; white-space:nowrap;}
.sev-critical{background:#82090c;color:#fff;}
.sev-vhigh{background:#cf222e;color:#fff;}
.sev-high{background:#f6766f;color:#5a0c0c;}
.sev-medium{background:#fff1cf;color:#7d4e00;}
.sev-low{background:#e6f2ff;color:#0a4a87;}
.sev-none{background:#eee;color:#555;}
.pri-p0{background:#82090c;color:#fff;}
.pri-p1{background:#cf222e;color:#fff;}
.pri-p2{background:#fff1cf;color:#7d4e00;}
.pri-p3{background:#e6f2ff;color:#0a4a87;}
.pri-none{background:#eee;color:#555;}
.muted{color:var(--muted);}
.verify{font-size:12px; font-weight:600; padding:2px 8px; border-radius:10px;}
.verify.pass{background:#dcfce7;color:#166534;}
.verify.fail{background:#fee2e2;color:#991b1b;}
details.linked{
  margin-top:4px; border-top:1px dashed var(--line); padding-top:10px;
}
details.linked summary{
  cursor:pointer; font-size:12.5px; font-weight:600; color:var(--accent);
  list-style:none; outline:none;
}
details.linked summary::-webkit-details-marker{display:none;}
details.linked summary:before{content:"\\25b8 "; color:var(--muted);}
details.linked[open] summary:before{content:"\\25be ";}
table.defects{
  width:100%; border-collapse:collapse; margin-top:8px; font-size:12px;
}
table.defects th{
  text-align:left; background:#f0f3f6; color:#44505a;
  font-weight:600; padding:6px 8px; border:1px solid var(--line);
  font-size:11px; text-transform:uppercase; letter-spacing:.4px;
}
table.defects td{padding:6px 8px; border:1px solid var(--line);
  vertical-align:top; word-break:break-word;}
table.defects tr:nth-child(even) td{background:#fafbfc;}
.defect-id a{color:var(--accent); text-decoration:none; font-weight:600;}
.defect-id a:hover{text-decoration:underline;}
.feedback{font-size:12px; color:#7d4e00; background:#fff8e6;
  border:1px solid #f0e0a8; border-radius:6px; padding:6px 8px; margin-top:6px;}
footer{margin-top:40px; padding-top:16px; border-top:1px solid var(--line);
  color:var(--muted); font-size:11.5px; text-align:center;}
@media print{
  body{background:#fff;}
  details.linked{border-top:1px solid #999;}
  details.linked:not([open]) table.defects{display:table;}
  article.tc{break-inside:avoid; box-shadow:none;}
  .wrap{max-width:100%; padding:0;}
}
"""


def render_summary_stats(total_tc, total_defects, n_themes, dropped):
    return (
        f'<div class="stat"><span class="n">{total_tc}</span>'
        f'<span class="l">Testcases</span></div>'
        f'<div class="stat"><span class="n">{total_defects}</span>'
        f'<span class="l">Linked defects</span></div>'
        f'<div class="stat"><span class="n">{n_themes}</span>'
        f'<span class="l">Theme clusters</span></div>'
        f'<div class="stat"><span class="n">{dropped}</span>'
        f'<span class="l">RSE filtered out</span></div>'
    )


def render_toc(themes):
    items = []
    for i, (theme, sub, members) in enumerate(themes):
        items.append(
            f'<a href="#theme-{i}">{esc(theme)} '
            f'<span class="cnt">({len(members)})</span></a>'
        )
    return f'<div class="toc-list">{"".join(items)}</div>'


def render_linked_defects(members):
    if not members:
        return ""
    rows_html = []
    for m in members:
        did = esc(m["defect_id"])
        link = octane_link(m["defect_id"])
        id_cell = (f'<a href="{link}" target="_blank">D{did}</a>'
                   if did else "—")
        rows_html.append(
            f"<tr>"
            f'<td class="defect-id">{id_cell}</td>'
            f"<td>{esc(m['defect_name'])}</td>"
            f"<td>{severity_badge(m['defect_severity'])}</td>"
            f"<td>{esc(m['assigned_ecu'])}</td>"
            f"<td>{esc(m['lead_model'])}</td>"
            f"<td>{fmt_sim(m['similarity_to_primary'])}</td>"
            f"</tr>"
        )
    body = "".join(rows_html)
    n = len(members)
    label = "linked defect" if n == 1 else "linked defects"
    return f"""
    <details class="linked">
      <summary>{n} {label} in this similarity cluster (click to expand)</summary>
      <table class="defects">
        <thead><tr>
          <th>Defect ID</th><th>Defect name</th><th>Severity</th>
          <th>ECU</th><th>Model</th><th>Sim.</th>
        </tr></thead>
        <tbody>{body}</tbody>
      </table>
    </details>"""


def render_testcase(primary, members, idx_in_theme):
    p = primary
    feedback = ""
    if p["verification_feedback"].strip():
        feedback = (
            f'<div class="feedback"><b>Verification feedback:</b> '
            f'{render_multiline(p["verification_feedback"])}</div>'
        )
    verify_cls = "pass" if p["verification_passed"].strip() == "True" else "fail"
    verify_txt = "Passed" if verify_cls == "pass" else "Needs review"
    tc_id = f'D{esc(p["defect_id"])}' if p["defect_id"].strip() else f"#{idx_in_theme}"
    linked = render_linked_defects(members)
    sw = esc(p["software_version"]) or "—"
    ecu = esc(p["assigned_ecu"]) or "—"
    model = esc(p["lead_model"]) or "—"

    return f"""
    <article class="tc">
      <div class="tc-bar">
        <span class="tc-title">{esc(p['test_name'])}</span>
        {priority_badge(p['test_priority'])}
        {severity_badge(p['defect_severity'])}
        <span class="verify {verify_cls}">{verify_txt}</span>
        <span class="meta">{tc_id}</span>
      </div>
      <div class="tc-body">
        <div class="field">
          <div class="field-label">Defect</div>
          <div class="field-content">
            <a href="{octane_link(p['defect_id'])}" target="_blank">D{esc(p['defect_id'])}</a>
            &mdash; {esc(p['defect_name'])}
          </div>
        </div>
        <div class="grid2">
          <div class="kv"><span class="k">Software version:</span> {sw}</div>
          <div class="kv"><span class="k">Assigned ECU:</span> {ecu}</div>
          <div class="kv"><span class="k">Lead model:</span> {model}</div>
          <div class="kv"><span class="k">Generated:</span> {esc(p['generated_at'][:10])}</div>
        </div>
        <div class="field">
          <div class="field-label">Test overview</div>
          <div class="field-content">{render_multiline(p['test_overview'])}</div>
        </div>
        <div class="grid2">
          <div class="field">
            <div class="field-label">Preconditions</div>
            <div class="field-content">{render_multiline(p['preconditions'])}</div>
          </div>
          <div class="field">
            <div class="field-label">Execution steps</div>
            <div class="field-content">{render_steps(p['execution_steps'])}</div>
          </div>
        </div>
        <div class="field">
          <div class="field-label">Expected results</div>
          <div class="field-content">{render_steps(p['expected_results'])}</div>
        </div>
        {feedback}
        {linked}
      </div>
    </article>"""


def render_html(themes, total_tc, total_defects, dropped, generated_at):
    toc = render_toc(themes)
    sections = []
    sev_rank = {"Critical": 0, "Very High": 1, "High": 2, "Medium": 3, "Low": 4}
    pri_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}

    def sort_key(item):
        p, mem = item
        return (
            pri_rank.get(p["test_priority"].strip(), 9),
            sev_rank.get(p["defect_severity"].strip(), 9),
            int(p["defect_id"]) if p["defect_id"].strip().isdigit() else 10**9,
        )

    for i, (theme, sub, members) in enumerate(themes):
        cards = []
        for j, (p, mem) in enumerate(sorted(members, key=sort_key)):
            cards.append(render_testcase(p, mem, j + 1))
        sections.append(
            f'<section class="theme" id="theme-{i}">'
            f'<div class="theme-head">'
            f"<h2>{esc(theme)}</h2>"
            f'<span class="sub">{esc(sub)}</span>'
            f'<span class="cnt">{len(members)} testcase'
            f'{"s" if len(members) != 1 else ""}</span>'
            f"</div>"
            f"{''.join(cards)}"
            f"</section>"
        )
    stats = render_summary_stats(total_tc, total_defects, len(themes), dropped)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>RSU Testcases — Consolidated Report</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <header class="report-head">
    <h1>RSU Testcases — Consolidated &amp; Clustered Report</h1>
    <p>Remote Software Upgrade regression testcases, deduplicated by similarity
       and clustered into functional themes. Generated {esc(generated_at)}.</p>
    <div class="stat-row">{stats}</div>
  </header>
  <nav class="toc">
    <h2>Theme clusters</h2>
    {toc}
  </nav>
  {''.join(sections)}
  <footer>
    Source: RSU_testcases_plain_20260901_final.csv &middot;
    RSE (Rear Seat Entertainment) functional defects filtered out &middot;
    Similarity clustering threshold: 0.84 &middot;
    Testcase shown first, linked defects collapsed below each testcase.
  </footer>
</div>
</body>
</html>"""


# --------------------------------------------------------------------------
# 5. Main
# --------------------------------------------------------------------------

def main():
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(here, SRC)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = os.path.join(here, f"RSU_testcases_consolidated_{stamp}.html")

    with open(src, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    total_rows = len(rows)
    groups = defaultdict(list)
    for r in rows:
        groups[r["group_id"]].append(r)

    kept = []
    dropped_primaries = 0
    for gid, members in groups.items():
        primaries = [m for m in members if m["is_primary"] == "True"]
        others = [m for m in members if m["is_primary"] != "True"]
        if not primaries:
            continue
        primary = primaries[0]
        if is_rsu(primary):
            kept.append((primary, others))
        else:
            dropped_primaries += 1

    theme_buckets = defaultdict(list)
    theme_subs = {}
    for primary, members in kept:
        theme, sub = classify_theme(primary)
        theme_buckets[theme].append((primary, members))
        theme_subs[theme] = sub

    themes = sorted(theme_buckets.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    themes_final = [(t, theme_subs[t], m) for t, m in themes]

    total_tc = len(kept)
    total_defects = sum(len(m) for _, m in kept)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html_out = render_html(
        themes_final, total_tc, total_defects, dropped_primaries, generated_at
    )
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_out)

    print(f"Source rows:           {total_rows}")
    print(f"Groups total:          {len(groups)}")
    print(f"RSU groups kept:       {total_tc}")
    print(f"RSE groups dropped:    {dropped_primaries}")
    print(f"Linked defects (non-primary, kept): {total_defects}")
    print(f"Theme clusters:        {len(themes_final)}")
    print()
    print("Theme distribution:")
    for t, s, m in themes_final:
        print(f"  {t:32s} {len(m):3d}   ({s})")
    print()
    print(f"WROTE: {out}")


if __name__ == "__main__":
    main()
