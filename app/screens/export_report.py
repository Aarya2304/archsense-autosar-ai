"""Export & Report screen (M8.15/M8.16): deterministic report generation.

Assembles the report from the M1-M7 subsystems via the report service and
offers JSON/CSV downloads (also written under the ignored ``data/exports/``
runtime path). No secrets are included in any export (D-045).
"""

from __future__ import annotations

import streamlit as st

from app import state
from app.services import (ServiceError, get_versions,
                          report_changes_to_csv, report_findings_to_csv,
                          report_impacts_to_csv, report_to_json)


def render() -> None:
    try:
        versions = get_versions()
    except ServiceError as exc:
        st.error(f"Database unavailable: {exc}")
        return
    if not versions:
        st.warning("No ingested documents. Run the M1 ingestion pipeline "
                   "first.")
        return

    reg_versions = [v["version"] for v in versions if v["has_registry"]]
    labels = [f"{v['document_name']} — v{v['version']}" for v in versions]

    c1, c2, c3 = st.columns(3)
    with c1:
        version = st.selectbox("Primary version", labels)
        version = versions[labels.index(version)]["version"]
    with c2:
        base = st.selectbox(
            "Comparison base (optional)", ["(none)"] + reg_versions,
            key="ex_rp_base")
        base = None if base == "(none)" else base
    with c3:
        target = st.selectbox(
            "Comparison target (optional)", ["(none)"] + reg_versions,
            index=min(1, len(reg_versions) - 1) if reg_versions else 0,
            key="ex_rp_target")
        target = None if target == "(none)" else target

    include = {
        "architecture": st.checkbox("Architecture summary / entities / "
                                    "relationships", True, key="rp_arch"),
        "findings": st.checkbox("Findings (fresh M6 run)", True,
                                key="rp_find"),
        "comparison": st.checkbox("Revision comparison (M7)", True,
                                  key="rp_comp"),
        "evidence": st.checkbox("Evidence / provenance index", True,
                                key="rp_ev"),
    }

    if st.button("Generate report", type="primary", key="rp_gen"):
        with st.spinner("Assembling report from backend subsystems…"):
            try:
                report = _build(version, base, target, include)
                st.session_state["as_last_report"] = report
            except ServiceError as exc:
                st.error(str(exc))
                return

    report = st.session_state.get("as_last_report")
    if report is None:
        st.info("Choose sections and generate the report.")
        return

    sel = report.get("selections", {})
    st.success(f"Report generated for v{sel.get('version')}"
               + (f" ({sel.get('base_version')} → "
                  f"{sel.get('target_version')})" if sel.get("base_version")
                  else "") + ".")

    st.subheader("Report preview")
    with st.expander("document_information"):
        st.json(report.get("document_information", {}))
    with st.expander("architecture_summary"):
        st.json(report.get("architecture_summary", {}))
    st.caption(f"entities: {len(report.get('entities', []))} · "
               f"relationships: {len(report.get('relationships', []))} · "
               f"findings: {len(report.get('findings', []))} · "
               f"revision_changes: {len(report.get('revision_changes', []))} "
               f"· potential_impacts: "
               f"{len(report.get('potential_impacts', []))} · "
               f"evidence entries: "
               f"{len(report.get('evidence_provenance', []))}")

    st.subheader("Downloads")
    json_str = report_to_json(report)
    st.download_button("Download JSON report", json_str,
                       file_name="archsense_report.json",
                       mime="application/json", key="rp_dl_json")
    csv_findings = report_findings_to_csv(report)
    if csv_findings:
        st.download_button("Download findings CSV", csv_findings,
                           file_name="archsense_findings.csv",
                           mime="text/csv", key="rp_dl_csvf")
    csv_changes = report_changes_to_csv(report)
    if csv_changes:
        st.download_button("Download revision changes CSV", csv_changes,
                           file_name="archsense_changes.csv",
                           mime="text/csv", key="rp_dl_csvc")
    csv_impacts = report_impacts_to_csv(report)
    if csv_impacts:
        st.download_button("Download impacts CSV", csv_impacts,
                           file_name="archsense_impacts.csv",
                           mime="text/csv", key="rp_dl_csvi")

    _write_exports(report)
    st.caption("Copies are also written to the ignored runtime directory "
               "`data/exports/` when a report is generated in this session.")


def _build(version: str, base: str | None, target: str | None,
           include: dict) -> dict:
    from app.services.app_services import generate_report
    return generate_report(version=version, base_version=base,
                           target_version=target, include=include)


def _write_exports(report: dict) -> None:
    try:
        from backend.config import EXPORTS_DIR
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        (EXPORTS_DIR / "archsense_report.json").write_text(
            report_to_json(report), encoding="utf-8")
        if (csv_f := report_findings_to_csv(report)):
            (EXPORTS_DIR / "archsense_findings.csv").write_text(
                csv_f, encoding="utf-8")
        if (csv_c := report_changes_to_csv(report)):
            (EXPORTS_DIR / "archsense_changes.csv").write_text(
                csv_c, encoding="utf-8")
        if (csv_i := report_impacts_to_csv(report)):
            (EXPORTS_DIR / "archsense_impacts.csv").write_text(
                csv_i, encoding="utf-8")
    except Exception:  # pragma: no cover - runtime path issue
        pass
