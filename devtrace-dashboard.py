#!/usr/bin/env python3
"""
devtrace-dashboard.py
Reads ~/.omega/omega.db and ~/.devtrace/decisions.md
Modes:
  --summary    print terminal summary (default)
  --serve      serve local web dashboard at http://localhost:7474
  --export     export to decisions-export.json
"""

import argparse
import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

OMEGA_DB      = Path.home() / ".omega" / "omega.db"
DECISIONS_DIR = Path.home() / ".devtrace" / "decisions"
DECISIONS_OLD = Path.home() / ".devtrace" / "decisions.md"  # legacy fallback
PORT          = 7474


# ── Data readers ───────────────────────────────────────────────────────────────

def proj_name(path: str) -> str:
    if not path:
        return "unknown"
    return path.rstrip("/").split("/")[-1] or "home"


def read_omega() -> dict:
    if not OMEGA_DB.exists():
        return {"memories": [], "sessions": [], "stats": {},
                "error": f"{OMEGA_DB} not found"}
    try:
        con = sqlite3.connect(str(OMEGA_DB))
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r["name"] for r in cur.fetchall()]
        stats  = {"tables": tables}
        memories, sessions = [], []

        if "memories" in tables:
            cur.execute("""
                SELECT node_id, content, project, session_id,
                       event_type, memory_type, created_at,
                       access_count, priority, extracted_keywords
                FROM memories WHERE status = 'active'
                ORDER BY created_at DESC LIMIT 500
            """)
            memories = [dict(r) for r in cur.fetchall()]
            for m in memories:
                m["project_name"] = proj_name(m.get("project") or "")

            cur.execute("SELECT COUNT(*) as c FROM memories WHERE status='active'")
            stats["memory_total"] = cur.fetchone()["c"]

            # Sessions grouped by session_id + project
            cur.execute("""
                SELECT session_id, project,
                       MIN(created_at) as started_at,
                       MAX(created_at) as ended_at,
                       COUNT(*) as memory_count,
                       GROUP_CONCAT(DISTINCT event_type) as event_types
                FROM memories
                WHERE status='active' AND session_id IS NOT NULL AND session_id != ''
                GROUP BY session_id, project
                ORDER BY started_at DESC
            """)
            raw_sessions = [dict(r) for r in cur.fetchall()]
            session_map  = {}
            for s in raw_sessions:
                s["project_name"] = proj_name(s.get("project") or "")
                s["memories"]     = []
                session_map[s["session_id"]] = s
            for m in memories:
                sid = m.get("session_id")
                if sid and sid in session_map:
                    session_map[sid]["memories"].append(m)

            # Orphan memories (no session_id) — manually saved by user
            # Group by date + project as synthetic sessions
            orphans = [m for m in memories if not m.get("session_id")]
            orphan_groups = {}
            for m in orphans:
                key = (m.get("created_at","")[:10], m.get("project",""))
                if key not in orphan_groups:
                    orphan_groups[key] = {
                        "session_id": f"manual-{key[0]}-{proj_name(key[1])}",
                        "project": key[1],
                        "project_name": proj_name(key[1]),
                        "started_at": m.get("created_at",""),
                        "ended_at": m.get("created_at",""),
                        "memory_count": 0,
                        "event_types": "",
                        "memories": [],
                        "is_manual": True,
                    }
                orphan_groups[key]["memories"].append(m)
                orphan_groups[key]["memory_count"] += 1
                if m.get("created_at","") > orphan_groups[key]["ended_at"]:
                    orphan_groups[key]["ended_at"] = m.get("created_at","")
            sessions = raw_sessions + list(orphan_groups.values())
            sessions.sort(key=lambda s: s.get("started_at",""), reverse=True)

            # Project breakdown
            cur.execute("""
                SELECT project, COUNT(*) as c,
                       MIN(created_at) as first_seen,
                       MAX(created_at) as last_seen,
                       COUNT(DISTINCT session_id) as session_count
                FROM memories WHERE status='active' AND project IS NOT NULL
                GROUP BY project ORDER BY last_seen DESC
            """)
            stats["projects"]      = [dict(r) for r in cur.fetchall()]
            for p in stats["projects"]:
                p["project_name"]  = proj_name(p["project"])
            stats["session_count"] = len(raw_sessions)
            stats["project_count"] = len(stats["projects"])

        edges = []
        if "edges" in tables:
            cur.execute("SELECT * FROM edges LIMIT 200")
            edges = [dict(r) for r in cur.fetchall()]
        stats["edge_count"] = len(edges)

        con.close()
        return {"memories": memories, "sessions": sessions,
                "edges": edges, "stats": stats}
    except Exception as e:
        return {"memories": [], "sessions": [], "stats": {}, "error": str(e)}


def parse_decisions_file(text: str, source_file: str = "") -> list:
    """Parse a single decisions markdown file into structured entries."""
    entries = []
    blocks  = re.split(r"(?=^## \[\d{4}-\d{2}-\d{2}\])", text, flags=re.MULTILINE)
    for block in blocks:
        block = block.strip()
        if not block or not block.startswith("## ["):
            continue
        entry = {"source_file": source_file}
        m = re.match(r"^## \[(\d{4}-\d{2}-\d{2})\]\s*(.+)$", block, re.MULTILINE)
        if m:
            entry["date"]  = m.group(1)
            entry["title"] = m.group(2).strip()
        for field, key in [
            ("Tool",           "tool"),
            ("Project",        "project"),
            ("Decision",       "decision"),
            ("Rationale",      "rationale"),
            ("Files affected", "files"),
            ("Open questions", "open_questions"),
        ]:
            fm = re.search(rf"\*\*{re.escape(field)}\*\*:\s*(.+)", block)
            if fm:
                entry[key] = fm.group(1).strip()
        # If project not in entry, infer from filename
        if "project" not in entry and source_file:
            stem = Path(source_file).stem
            if stem not in ("global", "decisions"):
                entry["project"] = stem
        entries.append(entry)
    return entries


def read_decisions() -> list:
    """Read all decision files from ~/.devtrace/decisions/ (or legacy single file)."""
    entries = []

    # New: per-project folder
    if DECISIONS_DIR.exists():
        for md_file in sorted(DECISIONS_DIR.glob("*.md")):
            if md_file.name == "README.md":
                continue
            try:
                text = md_file.read_text(encoding="utf-8")
                entries.extend(parse_decisions_file(text, str(md_file)))
            except Exception:
                pass

    # Legacy fallback: single decisions.md
    if not entries and DECISIONS_OLD.exists():
        text = DECISIONS_OLD.read_text(encoding="utf-8")
        entries.extend(parse_decisions_file(text, str(DECISIONS_OLD)))

    return sorted(entries, key=lambda x: x.get("date", ""), reverse=True)


# ── Terminal summary ───────────────────────────────────────────────────────────

def print_summary():
    omega   = read_omega()
    entries = read_decisions()
    C="\033[0;36m"; G="\033[0;32m"; Y="\033[1;33m"
    B="\033[1m";    R="\033[0m";    D="\033[2m"
    print(f"\n{B}DevTrace — knowledge provenance summary{R}")
    print("─"*52)
    st = omega.get("stats", {})
    if omega.get("error"):
        print(f"{Y}⚠ omega: {omega['error']}{R}")
    else:
        print(f"\n{C}omega-memory{R}")
        print(f"  Memories : {st.get('memory_total', 0)}")
        print(f"  Sessions : {st.get('session_count', 0)}")
        print(f"  Projects : {st.get('project_count', 0)}")
        for p in (st.get("projects") or [])[:5]:
            print(f"    {G}·{R} {p['project_name']:20s}  {p['c']} mem  {p['session_count']} sessions")
    print(f"\n{C}decisions.md{R}")
    if not entries:
        print(f"  {D}No entries yet.{R}")
    for e in entries[:8]:
        print(f"  {G}{e.get('date','?')}{R}  [{e.get('tool','?')}]  {B}{e.get('title','')}{R}  {D}({e.get('project','?')}){R}")
    print(f"\n  Run {B}devdash{R} → http://localhost:{PORT}\n")


# ── HTML ───────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DevTrace</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#0f0f11;--surf:#18181c;--surf2:#22222a;--bdr:rgba(255,255,255,.08);
  --acc:#7c6ff7;--acc2:#2dd4bf;--tx:#e4e4ef;--mut:#8888aa;
  --grn:#4ade80;--amb:#fbbf24;
  --font:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  --mono:'JetBrains Mono','Fira Code',monospace;
}
body{background:var(--bg);color:var(--tx);font-family:var(--font);font-size:14px;line-height:1.6}
.shell{max-width:1100px;margin:0 auto;padding:20px 16px}
.hdr{display:flex;align-items:center;gap:12px;margin-bottom:20px}
.hdr h1{font-size:18px;font-weight:600}
.badge{background:var(--acc);color:#fff;font-size:10px;border-radius:4px;padding:2px 7px;font-weight:700}
.sub{color:var(--mut);font-size:12px;margin-left:auto}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-bottom:20px}
.kpi{background:var(--surf);border:1px solid var(--bdr);border-radius:10px;padding:13px 14px}
.kpi .val{font-size:26px;font-weight:700;color:var(--acc)}
.kpi .lbl{color:var(--mut);font-size:11px;margin-top:2px}
.tabs{display:flex;gap:4px;margin-bottom:20px;border-bottom:1px solid var(--bdr)}
.tab{padding:8px 16px;font-size:13px;cursor:pointer;color:var(--mut);border-bottom:2px solid transparent;margin-bottom:-1px;transition:color .15s,border-color .15s;user-select:none}
.tab:hover{color:var(--tx)}
.tab.active{color:var(--acc);border-bottom-color:var(--acc)}
.panel{display:none}.panel.active{display:block}
.card{background:var(--surf);border:1px solid var(--bdr);border-radius:12px;overflow:hidden;margin-bottom:16px}
.chdr{padding:10px 14px;border-bottom:1px solid var(--bdr);font-size:11px;font-weight:700;color:var(--mut);text-transform:uppercase;letter-spacing:.06em;display:flex;align-items:center;gap:8px}
.dot{width:8px;height:8px;border-radius:50%}
.da{background:var(--acc)}.db{background:var(--acc2)}.dc{background:var(--grn)}.dd{background:var(--amb)}
.toolbar{padding:8px 14px;border-bottom:1px solid var(--bdr);display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.toolbar input,.toolbar select{background:var(--surf2);border:1px solid var(--bdr);border-radius:6px;padding:5px 9px;color:var(--tx);font-size:12px;outline:none;font-family:var(--font)}
.toolbar input{flex:1;min-width:160px}
.toolbar input:focus,.toolbar select:focus{border-color:var(--acc)}
.toolbar select option{background:var(--surf2)}

/* timeline */
.tl-day{margin-bottom:24px}
.tl-date{font-size:11px;font-weight:700;color:var(--mut);text-transform:uppercase;letter-spacing:.07em;margin-bottom:10px;padding-left:14px;border-left:2px solid var(--bdr)}
.tl-session{background:var(--surf);border:1px solid var(--bdr);border-radius:10px;margin-bottom:8px;overflow:hidden}
.tl-sess-hdr{padding:10px 14px;display:flex;align-items:center;gap:10px;cursor:pointer;transition:background .12s;flex-wrap:wrap}
.tl-sess-hdr:hover{background:var(--surf2)}
.proj-pill{font-size:11px;font-weight:700;padding:2px 8px;border-radius:4px;white-space:nowrap}
.tl-count{background:rgba(124,111,247,.15);color:var(--acc);font-size:11px;padding:1px 7px;border-radius:4px}
.tl-time{color:var(--mut);font-size:11px;font-family:var(--mono)}
.tl-dur{color:var(--mut);font-size:11px}
.tl-sid{color:var(--mut);font-size:10px;font-family:var(--mono);margin-left:auto}
.tl-body{display:none;border-top:1px solid var(--bdr)}
.tl-body.open{display:block}
.tl-mem{padding:9px 14px;border-bottom:1px solid var(--bdr);font-size:12px}
.tl-mem:last-child{border-bottom:none}
.tl-mc{color:var(--tx);margin-bottom:3px;line-height:1.5;word-break:break-word}
.tl-mm{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:3px}
.tl-ts{color:var(--mut);font-size:10px;font-family:var(--mono)}
.tl-et{font-size:10px;color:var(--acc2)}
.tl-kw{font-size:10px;color:var(--mut);opacity:.55}
.tl-dec{background:rgba(251,191,36,.07);border-left:3px solid var(--amb);padding:10px 14px;margin-bottom:8px;border-radius:0 8px 8px 0}
.tl-dec-title{font-size:13px;font-weight:600;color:var(--amb);margin-bottom:4px}
.tl-dec-rat{font-size:11px;color:var(--mut)}

/* projects */
.proj-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:14px;margin-bottom:16px}
.proj-card{background:var(--surf);border:1px solid var(--bdr);border-radius:10px;padding:14px;cursor:pointer;transition:border-color .15s}
.proj-card:hover{border-color:var(--acc)}
.proj-card h3{font-size:14px;font-weight:600;margin-bottom:6px}
.proj-stat{font-size:11px;color:var(--mut);margin-top:3px}
.proj-bar{margin-top:10px;height:3px;background:var(--surf2);border-radius:2px}
.proj-fill{height:100%;border-radius:2px;background:var(--acc)}
.proj-dates{font-size:10px;color:var(--mut);margin-top:6px;font-family:var(--mono)}
.detail{background:var(--surf2);border:1px solid var(--bdr);border-radius:10px;padding:16px;margin-bottom:16px;display:none}
.detail.open{display:block}
.detail h2{font-size:15px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:10px}
.det-close{margin-left:auto;cursor:pointer;color:var(--mut);font-size:20px;line-height:1}
.det-close:hover{color:var(--tx)}
.det-mem{padding:7px 0;border-bottom:1px solid var(--bdr);font-size:12px}
.mem-full{display:none}.mem-short{display:block}
.mem-expand{color:var(--acc);font-size:10px;cursor:pointer;margin-top:2px;display:inline-block}
.mem-expand:hover{text-decoration:underline}
.det-mem:last-child{border-bottom:none}

/* decisions */
.dec-item{padding:12px 14px;border-bottom:1px solid var(--bdr);transition:background .12s}
.dec-item:last-child{border-bottom:none}
.dec-item:hover{background:var(--surf2)}
.dec-top{display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.dec-title{font-weight:600;font-size:13px;flex:1;min-width:80px}
.dec-date{color:var(--mut);font-size:10px;font-family:var(--mono)}
.tag{border-radius:4px;padding:1px 6px;font-size:10px;font-weight:700}
.tc{background:rgba(124,111,247,.2);color:#a89ffa}
.tcu{background:rgba(45,212,191,.2);color:#5eead4}
.tcp{background:rgba(74,222,128,.2);color:#86efac}
.to{background:rgba(251,191,36,.2);color:#fcd34d}
.dproj{background:rgba(255,255,255,.07);color:var(--mut);border-radius:4px;padding:1px 6px;font-size:10px}
.dec-rat{color:var(--mut);font-size:11px;margin-top:5px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.dec-oq{color:var(--amb);font-size:10px;margin-top:3px}
.empty{padding:28px;text-align:center;color:var(--mut);font-size:13px}
footer{color:var(--mut);font-size:11px;text-align:center;margin-top:24px;padding-bottom:16px}

/* project color classes */
.pc0{background:rgba(124,111,247,.2);color:#a89ffa}
.pc1{background:rgba(45,212,191,.2);color:#5eead4}
.pc2{background:rgba(251,191,36,.2);color:#fcd34d}
.pc3{background:rgba(74,222,128,.2);color:#86efac}
.pc4{background:rgba(248,113,113,.2);color:#fca5a5}
.pc5{background:rgba(139,92,246,.2);color:#c4b5fd}
.pc6{background:rgba(34,211,238,.2);color:#67e8f9}
</style>
</head>
<body>
<div class="shell">
  <div class="hdr">
    <div>
      <div style="display:flex;align-items:center;gap:10px">
        <h1>DevTrace</h1><span class="badge">LIVE</span>
      </div>
      <div style="color:var(--mut);font-size:11px;margin-top:1px">AI coding tool knowledge provenance</div>
    </div>
    <div class="sub" id="ts"></div>
  </div>

  <div class="kpis" id="kpis"></div>

  <div class="tabs">
    <div class="tab active" data-tab="timeline" onclick="showTab(this)">Timeline</div>
    <div class="tab" data-tab="projects"  onclick="showTab(this)">Projects</div>
    <div class="tab" data-tab="memories"  onclick="showTab(this)">Memories</div>
    <div class="tab" data-tab="decisions" onclick="showTab(this)">Decisions</div>
  </div>

  <!-- TIMELINE -->
  <div class="panel active" id="tab-timeline">
    <div class="card">
      <div class="chdr"><span class="dot da"></span>Activity timeline — sessions by day</div>
      <div class="toolbar">
        <input  id="tl-srch"  type="search" placeholder="Search memory content…">
        <select id="tl-proj"><option value="">All projects</option></select>
      </div>
      <div id="tl-body" style="padding:14px"></div>
    </div>
  </div>

  <!-- PROJECTS -->
  <div class="panel" id="tab-projects">
    <div class="detail" id="proj-detail"></div>
    <div class="proj-grid" id="proj-grid"></div>
  </div>

  <!-- MEMORIES -->
  <div class="panel" id="tab-memories">
    <div class="card">
      <div class="chdr"><span class="dot db"></span>All omega observations</div>
      <div class="toolbar">
        <input  id="mem-srch"  type="search" placeholder="Search content…">
        <select id="mem-proj"><option value="">All projects</option></select>
        <select id="mem-type"><option value="">All types</option></select>
      </div>
      <div id="mem-body"></div>
    </div>
  </div>

  <!-- DECISIONS -->
  <div class="panel" id="tab-decisions">
    <div class="card">
      <div class="chdr"><span class="dot dd"></span>Decision log</div>
      <div class="toolbar">
        <input  id="dec-srch"  type="search" placeholder="Search decisions…">
        <select id="dec-proj"><option value="">All projects</option></select>
      </div>
      <div id="dec-body"></div>
    </div>
  </div>

  <footer>DevTrace · ~/.omega/omega.db + ~/.devtrace/decisions.md · refreshes every 60s</footer>
</div>

<script>
const RAW       = __DATA__;
const memories  = RAW.omega.memories  || [];
const sessions  = RAW.omega.sessions  || [];
const decisions = RAW.decisions       || [];
const st        = RAW.omega.stats     || {};
const omgProj   = st.projects         || [];

const COLORS = ['pc0','pc1','pc2','pc3','pc4','pc5','pc6'];
const projColor = {};
// Remap project display: "unknown" and home dir show as "⚠ no project" in UI
memories.forEach(m => {
  if (!m.project_name || m.project_name === 'unknown' || m.project_name === 'nmlemus') {
    m.project_display = '⚠ no project';
    m.project_name = m.project_name || 'unknown';
  } else {
    m.project_display = m.project_name;
  }
});
const projList  = [...new Set(memories.map(m => m.project_name).filter(p => p && p !== 'unknown'))].sort();
const typeList  = [...new Set(memories.map(m => m.event_type || m.memory_type).filter(Boolean))].sort();
const dProjList = [...new Set(decisions.map(d => d.project).filter(Boolean))].sort();
projList.forEach((p, i) => projColor[p] = COLORS[i % COLORS.length]);

function esc(s) { return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') }
function truncHTML(text, limit, uid) {
  if (!text) return '';
  if (text.length <= limit) return esc(text);
  return '<span id="'+uid+'-s">'+esc(text.slice(0,limit))
    +'\u2026 <span data-expand="'+uid+'" style="color:var(--acc);font-size:10px;cursor:pointer">show more</span>'
    +'</span><span id="'+uid+'-f" style="display:none">'+esc(text)+'</span>';
}
document.addEventListener('click', function(e) {
  if (e.target.dataset.expand) {
    var uid = e.target.dataset.expand;
    var s = document.getElementById(uid+'-s');
    var f = document.getElementById(uid+'-f');
    if (s) s.style.display = 'none';
    if (f) f.style.display = 'inline';
  }
});
function fmt(s) {
  if (!s) return '';
  try {
    // Normalize: replace space with T and ensure Z suffix for UTC
    const iso = s.trim().replace(' ', 'T').replace(/([+-]\d{2}:\d{2}|Z)?$/, v => v || 'Z');
    const d = new Date(iso);
    if (isNaN(d.getTime())) return s.slice(0,16);
    return d.toLocaleString(undefined, {
      year:'numeric', month:'2-digit', day:'2-digit',
      hour:'2-digit', minute:'2-digit', hour12:false
    });
  } catch(e) { return s.slice(0,16); }
}
function dur(a, b) {
  if (!a || !b) return '';
  const m = Math.round((new Date(b) - new Date(a)) / 60000);
  return m < 1 ? '<1m' : m < 60 ? `${m}m` : `${Math.round(m/60)}h${m%60?m%60+'m':''}`;
}
function toolTag(t) {
  if (!t) return '';
  const l = t.toLowerCase();
  if (l.includes('claude'))   return `<span class="tag tc">${esc(t)}</span>`;
  if (l.includes('cursor'))   return `<span class="tag tcu">${esc(t)}</span>`;
  if (l.includes('copilot'))  return `<span class="tag tcp">${esc(t)}</span>`;
  if (l.includes('opencode')) return `<span class="tag to">${esc(t)}</span>`;
  return `<span class="tag" style="background:rgba(255,255,255,.07);color:var(--mut)">${esc(t)}</span>`;
}

// KPIs
document.getElementById('ts').textContent = 'Updated ' + new Date().toLocaleTimeString();
const openQs = decisions.filter(d => d.open_questions && d.open_questions.toLowerCase() !== 'none').length;
document.getElementById('kpis').innerHTML = [
  { val: st.memory_total  || memories.length,  lbl: 'omega memories'   },
  { val: st.session_count || sessions.length,  lbl: 'sessions'         },
  { val: st.project_count || projList.length,  lbl: 'projects'         },
  { val: st.edge_count    || 0,                lbl: 'memory edges'     },
  { val: decisions.length,                     lbl: 'decisions logged' },
  { val: openQs,                               lbl: 'open questions'   },
].map(k => `<div class="kpi"><div class="val">${k.val}</div><div class="lbl">${k.lbl}</div></div>`).join('');

// populate selects
function fillSelect(id, items) {
  const el = document.getElementById(id);
  items.forEach(v => { const o = document.createElement('option'); o.value = o.textContent = v; el.appendChild(o); });
}
fillSelect('tl-proj',  projList);
fillSelect('mem-proj', projList);
fillSelect('mem-type', typeList);
fillSelect('dec-proj', dProjList);

// ── TIMELINE ─────────────────────────────────────────────────────────────────
function buildTL() {
  const sq = document.getElementById('tl-srch').value.toLowerCase();
  const sp = document.getElementById('tl-proj').value;

  let sess = sessions;
  if (sp) sess = sess.filter(s => s.project_name === sp);

  // group sessions by date
  const byDate = {};
  sess.forEach(s => {
    const d = (s.started_at || '').slice(0, 10);
    if (!byDate[d]) byDate[d] = [];
    byDate[d].push(s);
  });

  // group decisions by date
  const decByDate = {};
  decisions.forEach(d => {
    if (!d.date) return;
    if (sp && !(d.project || '').includes(sp)) return;
    if (!decByDate[d.date]) decByDate[d.date] = [];
    decByDate[d.date].push(d);
  });

  const dates = [...new Set([...Object.keys(byDate), ...Object.keys(decByDate)])].sort().reverse();
  if (!dates.length) return '<div class="empty">No activity yet — open Claude Code inside a project folder to start capturing memories</div>';

  return dates.map(date => {
    const label = new Date(date + 'T12:00:00').toLocaleDateString('en-US',
      { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });

    // Decision markers for this day
    const decHtml = (decByDate[date] || []).map(d => `
      <div class="tl-dec">
        <div class="tl-dec-title">📌 ${esc(d.title || 'Decision')}</div>
        ${d.rationale ? `<div class="tl-dec-rat">${esc(d.rationale.slice(0, 160))}</div>` : ''}
        <div class="tl-mm" style="margin-top:6px">
          ${toolTag(d.tool)}
          ${d.project ? `<span class="dproj">${esc(d.project)}</span>` : ''}
        </div>
      </div>`).join('');

    // Session blocks
    const sessHtml = (byDate[date] || []).map(s => {
      const pc   = projColor[s.project_name] || 'pc0';
      const mems = (s.memories || []).filter(m => !sq || (m.content || '').toLowerCase().includes(sq));
      if (sq && !mems.length) return '';

      const memRows = mems.slice(0, 40).map(m => `
        <div class="tl-mem">
          <div class="tl-mc">${truncHTML(m.content||'', 320, 'tm'+Math.random().toString(36).slice(2))}</div>
          <div class="tl-mm">
            <span class="tl-ts">${esc(fmt(m.created_at))}</span>
            ${m.event_type  ? `<span class="tl-et">${esc(m.event_type)}</span>` : ''}
            ${m.extracted_keywords ? `<span class="tl-kw">${esc((m.extracted_keywords||'').slice(0,60))}</span>` : ''}
          </div>
        </div>`).join('');

      return `<div class="tl-session">
        <div class="tl-sess-hdr" onclick="this.nextElementSibling.classList.toggle('open')">
          ${s.project_name === 'unknown' || !s.project_name
            ? `<span class="proj-pill" style="background:rgba(248,113,113,.2);color:#fca5a5">⚠ no project</span>`
            : `<span class="proj-pill ${pc}">${esc(s.project_name)}</span>`}
          <span class="tl-time">${esc(fmt(s.started_at))}</span>
          <span class="tl-dur">${dur(s.started_at, s.ended_at)}</span>
          <span class="tl-count">${s.memory_count} memories</span>
          ${s.is_manual ? `<span style="font-size:10px;background:rgba(251,191,36,.2);color:var(--amb);padding:1px 6px;border-radius:4px">manually saved</span>` : `<span class="tl-sid">${esc((s.session_id||'').slice(0,14))}…</span>`}
        </div>
        <div class="tl-body">${memRows || '<div class="empty" style="padding:10px 14px;font-size:12px">No memories match filter</div>'}</div>
      </div>`;
    }).filter(Boolean).join('');

    if (!decHtml && !sessHtml) return '';
    return `<div class="tl-day">
      <div class="tl-date">${label}</div>
      ${decHtml}${sessHtml}
    </div>`;
  }).filter(Boolean).join('') || '<div class="empty">No results</div>';
}

function renderTL() { document.getElementById('tl-body').innerHTML = buildTL(); }
document.getElementById('tl-srch').addEventListener('input',  renderTL);
document.getElementById('tl-proj').addEventListener('change', renderTL);
renderTL();

// ── PROJECTS ─────────────────────────────────────────────────────────────────
const maxMem = Math.max(...omgProj.map(p => p.c), 1);
document.getElementById('proj-grid').innerHTML = omgProj.length
  ? omgProj.map((p, i) => {
      const pc  = COLORS[i % COLORS.length];
      const dec = decisions.filter(d => (d.project || '').includes(p.project_name)).length;
      return `<div class="proj-card" onclick="showProj('${esc(p.project_name)}')">
        <h3><span class="proj-pill ${pc}">${esc(p.project_name)}</span></h3>
        <div class="proj-stat">${p.c} memories · ${p.session_count} sessions · ${dec} decisions</div>
        <div class="proj-bar"><div class="proj-fill" style="width:${Math.round(p.c/maxMem*100)}%"></div></div>
        <div class="proj-dates">Last: ${esc(fmt(p.last_seen))}<br>First: ${esc(fmt(p.first_seen))}</div>
      </div>`;
    }).join('')
  : '<div class="empty">No projects yet</div>';

function showProj(name) {
  const det   = document.getElementById('proj-detail');
  const p     = omgProj.find(x => x.project_name === name) || {};
  const pc    = projColor[name] || 'pc0';
  const pmems = memories.filter(m => m.project_name === name);
  const pdecs = decisions.filter(d => (d.project || '').includes(name));
  det.innerHTML = `
    <h2>
      <span class="proj-pill ${pc}">${esc(name)}</span>
      <span style="font-weight:400;color:var(--mut);font-size:13px">${p.c||0} memories · ${p.session_count||0} sessions</span>
      <span class="det-close" onclick="document.getElementById('proj-detail').classList.remove('open')">×</span>
    </h2>
    <div style="color:var(--mut);font-size:11px;margin-bottom:12px;font-family:var(--mono)">${esc(p.project||'')}</div>
    ${pdecs.length ? `
      <div style="font-size:11px;font-weight:700;color:var(--mut);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px">Decisions (${pdecs.length})</div>
      ${pdecs.map(d => `<div class="tl-dec">
        <div class="tl-dec-title">${esc(d.date||'')} — ${esc(d.title||'')}</div>
        ${d.rationale ? `<div class="tl-dec-rat">${esc(d.rationale.slice(0,180))}</div>` : ''}
      </div>`).join('')}` : ''}
    <div style="font-size:11px;font-weight:700;color:var(--mut);text-transform:uppercase;letter-spacing:.06em;margin:12px 0 8px">Recent memories (${pmems.length})</div>
    ${pmems.slice(0,20).map((m,i) => {
      const full = m.content || '';
      const short = full.length > 280 ? full.slice(0,280) : full;
      const needsExpand = full.length > 280;
      const uid = 'dm'+i+Date.now();
      return `<div class="det-mem">
        <div style="margin-bottom:2px;font-size:12px">
          ${truncHTML(full, 280, uid)}
        </div>
        <span style="font-size:10px;color:var(--mut);font-family:var(--mono)">${esc(fmt(m.created_at))}</span>
        ${m.event_type ? `<span style="font-size:10px;color:var(--acc2);margin-left:8px">${esc(m.event_type)}</span>` : ''}
      </div>`;
    }).join('')}
  `;
  det.classList.add('open');
  det.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ── MEMORIES ─────────────────────────────────────────────────────────────────
function renderMem() {
  const sq = document.getElementById('mem-srch').value.toLowerCase();
  const sp = document.getElementById('mem-proj').value;
  const st2= document.getElementById('mem-type').value;
  let list = memories;
  if (sq)  list = list.filter(m => (m.content||'').toLowerCase().includes(sq));
  if (sp)  list = list.filter(m => m.project_name === sp);
  if (st2) list = list.filter(m => (m.event_type||m.memory_type) === st2);
  document.getElementById('mem-body').innerHTML = list.length
    ? list.slice(0, 100).map(m => {
        const pc = projColor[m.project_name] || 'pc0';
        return `<div class="tl-mem" style="padding:10px 14px;border-bottom:1px solid var(--bdr)">
          <div class="tl-mc">${truncHTML(m.content||'', 300, 'mm'+Math.random().toString(36).slice(2))}</div>
          <div class="tl-mm" style="margin-top:4px">
            <span class="tl-ts">${esc(fmt(m.created_at))}</span>
            <span class="proj-pill ${pc}" style="font-size:10px;padding:1px 6px">${esc(m.project_name)}</span>
            ${m.event_type ? `<span class="tl-et">${esc(m.event_type)}</span>` : ''}
            ${m.extracted_keywords ? `<span class="tl-kw">${esc((m.extracted_keywords||'').slice(0,60))}</span>` : ''}
          </div>
        </div>`;
      }).join('')
    : '<div class="empty">No memories match</div>';
}
document.getElementById('mem-srch').addEventListener('input',  renderMem);
document.getElementById('mem-proj').addEventListener('change', renderMem);
document.getElementById('mem-type').addEventListener('change', renderMem);
renderMem();

// ── DECISIONS ────────────────────────────────────────────────────────────────
// Merge structured decisions.md entries with omega decision-type memories
const omegaDecisions = memories
  .filter(m => (m.event_type || '').toLowerCase() === 'decision')
  .map(m => ({
    _source:   'omega',
    date:      (m.created_at || '').slice(0, 10),
    title:     m.content.split('\n')[0].slice(0, 120),
    tool:      'Claude Code',
    project:   m.project_name || m.project || '',
    rationale: m.content,
    open_questions: null,
  }));

// Combine: structured md entries first, then omega decisions not already covered
const allDecisions = [
  ...decisions,
  ...omegaDecisions,
].sort((a, b) => (b.date||'').localeCompare(a.date||''));

// Populate project filter with all projects including omega decision projects
const allDecProjects = [...new Set(allDecisions.map(d => d.project).filter(Boolean))].sort();
const decProjSel = document.getElementById('dec-proj');
// clear existing options except "All projects"
while (decProjSel.options.length > 1) decProjSel.remove(1);
allDecProjects.forEach(v => { const o = document.createElement('option'); o.value = o.textContent = v; decProjSel.appendChild(o); });

function renderDec() {
  const sq = document.getElementById('dec-srch').value.toLowerCase();
  const sp = document.getElementById('dec-proj').value;
  let list = allDecisions;
  if (sq) list = list.filter(d => ['title','tool','project','rationale','decision'].some(k => (d[k]||'').toLowerCase().includes(sq)));
  if (sp) list = list.filter(d => (d.project||'').includes(sp));
  document.getElementById('dec-body').innerHTML = list.length
    ? list.map(d => {
        const isOmega = d._source === 'omega';
        const pc = projColor[d.project] || 'pc0';
        return `<div class="dec-item">
          <div class="dec-top">
            <span class="dec-title">${esc(d.title||'Untitled')}</span>
            <span class="dec-date">${esc(d.date||'')}</span>
            ${isOmega
              ? `<span class="tag" style="background:rgba(45,212,191,.2);color:#5eead4">omega</span>`
              : toolTag(d.tool)}
            ${d.project ? `<span class="proj-pill ${pc}" style="font-size:10px;padding:1px 6px">${esc(d.project)}</span>` : ''}
          </div>
          ${d.rationale && !isOmega ? `<div class="dec-rat">${esc(d.rationale)}</div>` : ''}
          ${isOmega ? `<div class="dec-rat">${truncHTML(d.rationale||'', 240, 'od'+Math.random().toString(36).slice(2))}</div>` : ''}
          ${d.open_questions && d.open_questions.toLowerCase()!=='none'
              ? `<div class="dec-oq">⚠ open: ${esc(d.open_questions)}</div>` : ''}
        </div>`;
      }).join('')
    : '<div class="empty">No decisions yet — decisions from both decisions/ files and omega are shown here</div>';
}
document.getElementById('dec-srch').addEventListener('input',  renderDec);
document.getElementById('dec-proj').addEventListener('change', renderDec);
renderDec();

// ── Tabs ──────────────────────────────────────────────────────────────────────
function showTab(el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-' + el.dataset.tab).classList.add('active');
}

setTimeout(() => location.reload(), 60000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            data = {"decisions": read_decisions(), "omega": read_omega()}
            data["omega"]["db_path"] = str(OMEGA_DB)
            html = HTML.replace("__DATA__", json.dumps(data, default=str))
            self.wfile.write(html.encode())
        elif self.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {"decisions": read_decisions(), "omega": read_omega()}
            self.wfile.write(json.dumps(data, default=str).encode())
        else:
            self.send_response(404)
            self.end_headers()


def main():
    parser = argparse.ArgumentParser(description="DevTrace dashboard")
    parser.add_argument("--summary", action="store_true")
    parser.add_argument("--serve",   action="store_true")
    parser.add_argument("--export",  action="store_true")
    parser.add_argument("--port",    type=int, default=PORT)
    args = parser.parse_args()

    if args.export:
        data = {"decisions": read_decisions(), "omega": read_omega()}
        out  = Path("decisions-export.json")
        out.write_text(json.dumps(data, indent=2, default=str))
        print(f"Exported to {out}")
        return

    if args.serve:
        import webbrowser
        url = f"http://localhost:{args.port}"
        print(f"DevTrace dashboard → {url}  (Ctrl+C to stop)")
        webbrowser.open(url)
        server = HTTPServer(("localhost", args.port), Handler)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped.")
        return

    print_summary()


if __name__ == "__main__":
    main()
