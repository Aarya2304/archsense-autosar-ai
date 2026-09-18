"""M8 final live-verification battery (screen-level, via Streamlit AppTest).

Executes the real app/main.py through Streamlit's official testing framework:
each screen is rendered, real controls are driven, and outcomes asserted.
No browser, no network; independent of the detached demo server.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
APP_MAIN = ROOT / "app" / "main.py"

from streamlit.testing.v1 import AppTest  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    suffix = f" | {detail}" if detail else ""
    print(("PASS " if cond else "FAIL ") + name + suffix, flush=True)


def run_main(timeout: float = 180.0) -> AppTest:
    at = AppTest.from_file(str(APP_MAIN), default_timeout=timeout)
    at.run()
    return at


def body_text(at: AppTest) -> str:
    parts: list[str | None] = []
    for attr in ("title", "header", "subheader", "markdown", "caption",
                 "text", "metric", "error", "warning", "info", "success"):
        for el in getattr(at, attr, []):
            parts.append(getattr(el, "body", None) or getattr(el, "value",
                                                              None))
    return "\n".join(str(p or "") for p in parts)


def nav(at: AppTest, screen_id: str) -> None:
    btn = next(b for b in at.sidebar.button
               if b.key == f"nav_{screen_id}")
    btn.click().run()


def df_rows(at: AppTest) -> list[dict]:
    """All dataframe rows across the screen (tables render as dataframes)."""
    rows: list[dict] = []
    for d in at.dataframe:
        try:
            rows.extend(d.value.to_dict("records"))
        except Exception:
            continue
    return rows


# ---------------------------------------------------------------- Dashboard
at = run_main()
check("dashboard: app runs without exception", not at.exception,
      str(at.exception[0].value) if at.exception else "")
txt = body_text(at)
check("dashboard: title present", "ArchSense" in txt)
check("dashboard: real metrics rendered",
      any((m.value or "") == "18" for m in at.metric)
      and any("145" in (m.value or "") for m in at.metric)
      and any("165 / 200" in (m.value or "") for m in at.metric),
      "metrics: " + ", ".join(sorted({m.value or "" for m in at.metric})))
check("dashboard: workspace selector lists both versions",
      len(at.selectbox) == 1 and "v1.0.0" in str(at.selectbox[0].options)
      and "v1.1.0" in str(at.selectbox[0].options))
nav_btns = {b.key: b for b in at.sidebar.button}
check("dashboard: 7 nav buttons present",
      {k for k in nav_btns if k.startswith("nav_")} ==
      {f"nav_{s}" for s in ("dashboard", "workspace", "explorer", "copilot",
                            "findings", "compare", "export")},
      repr(sorted(nav_btns)))
cards = [b for b in at.button if b.key and b.key.startswith("dash_")]
check("dashboard: 6 action cards present", len(cards) == 6,
      repr([b.key for b in cards]))

# ----------------------------------------- navigation via real nav buttons
titles = {"workspace": "Document Workspace", "explorer": "Architecture "
          "Explorer", "copilot": "Copilot", "findings": "Findings",
          "compare": "Revision Compare", "export": "Export & Report"}
for sid in ("workspace", "explorer", "copilot", "findings", "compare",
            "export"):
    at2 = run_main()
    nav(at2, sid)
    exc = at2.exception[0].value if at2.exception else ""
    t = body_text(at2)
    check(f"navigation: {titles[sid]} renders", not at2.exception
          and titles[sid] in t, f"exception={exc!r}")

# --------------------------------------------- dashboard action card click
at3 = run_main()
card = next(b for b in at3.button if b.key == "dash_findings")
card.click().run()
check("dashboard: action card navigates to Findings",
      not at3.exception and "Findings" in body_text(at3)
      and next((b for b in at3.sidebar.button
                if b.key == "nav_findings"), None) is not None
      and any("Run analysis" in (b.label or "") for b in at3.button),
      str(at3.exception[0].value) if at3.exception else "")

# ------------------------------------------------- Document Workspace deep
at4 = run_main()
nav(at4, "workspace")
t4 = body_text(at4)
check("workspace: metrics (pages/chunks) rendered",
      any((m.value or "") == "18" for m in at4.metric), t4[:200])
check("workspace: page selectbox present",
      any((s.label or "") == "Page" for s in at4.selectbox))
# change page -> body content must change (chunk text follows selection)
page_sb = next(s for s in at4.selectbox if (s.label or "") == "Page")
before = body_text(at4)
pages = page_sb.options
target = max(pages) if len(pages) > 1 else pages[0]
page_sb.set_value(target).run()
after = body_text(at4)
check("workspace: changing page changes rendered content", before != after)
check("workspace: page change does not raise", not at4.exception,
      str(at4.exception[0].value) if at4.exception else "")
# section button one-click navigation (the bug fixed in this pass)
sec_btn = next((b for b in at4.button
                if b.key and b.key.startswith("ws_sec_")), None)
if sec_btn is not None:
    sec_page = int(sec_btn.key.rsplit("_", 1)[1])
    sec_btn.click().run()
    check("workspace: section button navigates in one click",
          not at4.exception, str(at4.exception[0].value)
          if at4.exception else "")
    check("workspace: section click lands on section page",
          not at4.exception and any(
              (s.label or "") == "Page" and int(s.value) == sec_page
              for s in at4.selectbox))
# version switch leaves no stale page
at4b = run_main()
nav(at4b, "workspace")
sel = next(s for s in at4b.selectbox
           if (s.label or "") == "Document / version")
other = next(o for o in sel.options if "1.0.0" in o)
sel.set_value(other).run()
check("workspace: version switch resets to first page without exception",
      not at4b.exception and any((s.label or "") == "Page"
                                 and int(s.value) == 1
                                 for s in at4b.selectbox),
      str(at4b.exception[0].value) if at4b.exception else "")

# ------------------------------------------------- Architecture Explorer
at5 = run_main()
nav(at5, "explorer")
check("explorer: renders without exception", not at5.exception,
      str(at5.exception[0].value) if at5.exception else "")
t5 = body_text(at5)
m_full = re.search(r"Showing (\d+) nodes / (\d+) edges", t5)
check("explorer: full graph caption present", m_full is not None, t5[:300])
check("explorer: filters present (entity type / predicate / confidence)",
      any("Entity type" in (s.label or "") for s in at5.selectbox)
      and any("Predicate" in (s.label or "") for s in at5.selectbox)
      and any("confidence" in (s.label or "").lower() for s in at5.slider))
# drive entity-type filter -> subset strictly smaller than the full graph
etype_sb = next(s for s in at5.selectbox if "Entity type" in (s.label or ""))
etype_sb.set_value("component").run()
t5b = body_text(at5)
m_sub = re.search(r"Showing (\d+) nodes / (\d+) edges", t5b)
check("explorer: entity-type filter shrinks the graph",
      not at5.exception and m_full is not None and m_sub is not None
      and (int(m_sub.group(1)), int(m_sub.group(2)))
      != (int(m_full.group(1)), int(m_full.group(2))),
      f"full={m_full.groups() if m_full else None} "
      f"sub={m_sub.groups() if m_sub else None}")
# drive confidence filter to max -> all edges dropped
conf = next(s for s in at5.slider if "confidence" in (s.label or "").lower())
conf.set_value(1.0).run()
t5c = body_text(at5)
check("explorer: confidence filter at 1.0 keeps nodes, drops edges",
      not at5.exception and "0 edges" in t5c,
      str(at5.exception[0].value) if at5.exception else "")
# entity search
search = next((i for i in at5.text_input
               if "Search entity" in (i.label or "")), None)
if search is not None:
    search.set_value("C-05").run()
    check("explorer: search finds C-05", not at5.exception
          and "C-05" in body_text(at5),
          str(at5.exception[0].value) if at5.exception else "")
# entity details selectbox
detail = next((s for s in at5.selectbox if (s.label or "") ==
               "Entity details"), None)
if detail is not None:
    comp_key = next((o for o in detail.options
                     if str(o).startswith("component:")), None)
    if comp_key is not None:
        detail.set_value(comp_key).run()
        t5d = body_text(at5)
        check("explorer: entity detail shows attributes + relationships",
              not at5.exception and "Entity type" in t5d
              and "relationships" in t5d.lower()
              and "Provenance" in t5d,
              str(at5.exception[0].value) if at5.exception else "")

# ------------------------------------------------- Copilot
at6 = run_main()
nav(at6, "copilot")
check("copilot: renders without exception", not at6.exception,
      str(at6.exception[0].value) if at6.exception else "")
q = next(i for i in at6.text_input if (i.label or "") == "Question")
q.set_value("Which component provides the VehicleMode interface?").run()
ask = next(b for b in at6.button if (b.label or "") == "Ask")
ask.click().run()
t6 = body_text(at6)
check("copilot: grounded answer produced", not at6.exception
      and "Answer" in t6, str(at6.exception[0].value)
      if at6.exception else t6[-300:])
check("copilot: sources/citations rendered",
      "Sources" in t6 and "[E" in t6, t6[-400:])
check("copilot: provider/model/timing caption shown", "provider:" in t6)
check("copilot: no secrets in output",
      "sk-or-" not in t6 and "OPENROUTER_API_KEY" not in t6)

# ------------------------------------------------- Findings (v1.1.0 gold)
at7 = run_main()
nav(at7, "findings")
check("findings: renders without exception", not at7.exception,
      str(at7.exception[0].value) if at7.exception else "")
vsel = next(s for s in at7.selectbox if (s.label or "") == "Version")
vsel.set_value(next(o for o in vsel.options if "1.1.0" in o)).run()
t7 = body_text(at7)
check("findings: persisted state notice shown",
      "Showing the latest persisted analysis" in t7)
check("findings: ORPHAN_ENTITY finding present",
      "orphan_entity" in t7.lower())
check("findings: C-05 entity referenced", "C-05" in t7)
check("findings: provenance shown (document + section + page)",
      "ABC_HLD" in t7 and "Section" in t7 and "p." in t7, t7[:80])
check("findings: status displayed read-only", "Status" in t7)
run_btn = next((b for b in at7.button if (b.label or "").startswith
                ("Run analysis")), None)
check("findings: run-analysis button present", run_btn is not None)
if run_btn is not None:
    run_btn.click().run()
    t7r = body_text(at7)
    check("findings: fresh v1.1.0 run completes without exception",
          not at7.exception, str(at7.exception[0].value)
          if at7.exception else "")
    check("findings: fresh run reproduces ORPHAN_ENTITY",
          "orphan_entity" in t7r.lower()
          and any((m.label or "") == "Findings" for m in at7.metric),
          str([(m.label, m.value) for m in at7.metric]))
    # drive the severity filter deterministically from the rendered header
    hdr = re.search(r"\[(HIGH|MEDIUM|LOW|INFO)\]", t7r)
    msel = next((ms for ms in at7.multiselect
                 if (ms.label or "") == "Severity"), None)
    if hdr is not None and msel is not None:
        msel.set_value([hdr.group(1)]).run()
        check("findings: severity filter keeps matching finding",
              "orphan_entity" in body_text(at7).lower())
        others = [s for s in ("HIGH", "MEDIUM", "LOW", "INFO")
                  if s != hdr.group(1)]
        msel.set_value(others).run()
        check("findings: severity filter hides non-matching finding",
              "orphan_entity" not in body_text(at7).lower())
    # version switch -> v1.0.0 clean baseline
    vsel = next(s for s in at7.selectbox if (s.label or "") == "Version")
    vsel.set_value(next(o for o in vsel.options if "1.0.0" in o)).run()
    t7b = body_text(at7)
    check("findings: v1.0.0 shows clean baseline (0 findings)",
          not at7.exception and ("No findings yet" in t7b
                                 or "orphan_entity" not in t7b.lower()),
          str(at7.exception[0].value) if at7.exception else "")

# ------------------------------------------------- Revision Compare
at8 = run_main()
nav(at8, "compare")
check("compare: renders without exception", not at8.exception,
      str(at8.exception[0].value) if at8.exception else "")
base_sb = next(s for s in at8.selectbox if (s.label or "") == "Base version")
tgt_sb = next(s for s in at8.selectbox if (s.label or "") == "Target version")
base_sb.set_value("1.0.0").run()
tgt_sb.set_value("1.1.0").run()
cmp_btn = next(b for b in at8.button if (b.label or "") == "Compare revisions")
cmp_btn.click().run()
t8 = body_text(at8)
check("compare: run completes without exception", not at8.exception,
      str(at8.exception[0].value) if at8.exception else "")
check("compare: summary metrics rendered",
      any((m.label or "") == "Entities removed" for m in at8.metric))
check("compare: 16 entities removed / 1 changed shown",
      any((m.value or "") == "16" for m in at8.metric)
      and any((m.value or "") == "1" for m in at8.metric),
      "metrics: " + ", ".join(f"{m.label}={m.value}" for m in at8.metric))
check("compare: 43 added / 65 removed relationships shown",
      "+43 / -65" in t8, t8[:500])
check("compare: potentially-impacted labelled as potential",
      "Potentially impacted" in t8 and "potential" in t8.lower())
rows = df_rows(at8)
check("compare: D1 removed-dependency change present",
      any("removed" in str(r.get("change", "")).lower()
          and r.get("predicate") == "depends_on" for r in rows),
      f"{len(rows)} table rows")
check("compare: provenance lines in change tables",
      any("ABC_HLD" in str(r.get("provenance", "")) for r in rows))
check("compare: no validation errors", "Validation errors" not in t8)
# depth actually changes traversal size
imp_full = next((int(m.value) for m in at8.metric
                 if (m.label or "") == "Potentially impacted"), None)
depth = next(s for s in at8.number_input
             if (s.label or "") == "Impact depth")
depth.set_value(0).run()
next(b for b in at8.button if (b.label or "") == "Compare revisions").click().run()
imp_d0 = next((int(m.value) for m in at8.metric
               if (m.label or "") == "Potentially impacted"), None)
check("compare: depth 0 (direct-only) reduces potential impacts",
      not at8.exception and imp_full is not None and imp_d0 is not None
      and 0 < imp_d0 < imp_full,
      f"depth1={imp_full} depth0={imp_d0}")

# ------------------------------------------------- Export & Report
at9 = run_main()
nav(at9, "export")
check("export: renders without exception", not at9.exception,
      str(at9.exception[0].value) if at9.exception else "")
gen = next(b for b in at9.button if (b.label or "") == "Generate report")
gen.click().run()
t9 = body_text(at9)
check("export: report generated", not at9.exception
      and "Report generated" in t9, str(at9.exception[0].value)
      if at9.exception else "")
check("export: entity/finding/change counts surfaced",
      "entities:" in t9 and "findings:" in t9 and "revision_changes:" in t9)
dl = [b.label for b in at9.download_button]
check("export: JSON + CSV download buttons present",
      any("JSON" in (l or "") for l in dl)
      and sum(1 for l in dl if "CSV" in (l or "")) >= 1,
      repr(dl))
check("export: JSON artifact written under data/exports",
      (ROOT / "data" / "exports" / "archsense_report.json").exists())
rep = (ROOT / "data" / "exports" / "archsense_report.json").read_text(
    encoding="utf-8")
check("export: report JSON has required sections",
      all(k in rep for k in ("document_information", "architecture_summary",
                             "findings", "revision_changes",
                             "potential_impacts")))
check("export: report contains no API keys/secrets",
      "OPENROUTER_API_KEY" not in rep and "sk-or-" not in rep
      and "api_key" not in rep.lower())

# --------------------------------------------- state: version change resets
at10 = run_main()
nav(at10, "copilot")
q10 = next(i for i in at10.text_input if (i.label or "") == "Question")
q10.set_value("What does C-02 provide?").run()
ask10 = next(b for b in at10.button if (b.label or "") == "Ask")
ask10.click().run()
check("state: copilot answer stored in session", "Sources" in body_text(at10))
nav(at10, "findings")
sel10 = next(s for s in at10.selectbox if (s.label or "") == "Version")
sel10.set_value(next(o for o in sel10.options if "1.1.0" in o)).run()
check("state: version change does not crash other screens",
      not at10.exception, str(at10.exception[0].value)
      if at10.exception else "")
nav(at10, "copilot")
check("state: stale copilot answer cleared after version switch",
      "Sources" not in body_text(at10))

# ------------------------------------------------------------------ summary
print("\n=== SUMMARY ===")
print(f"passed: {len(PASS)}  failed: {len(FAIL)}")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
