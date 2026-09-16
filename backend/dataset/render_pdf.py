"""
PDF renderer for the synthetic HLD corpus (M0).

Produces realistic AUTOSAR-style HLD PDFs from the source-of-truth model:
numbered sections, port/signal/dependency tables, cross-references, revision
history, running headers/footers, and (for v2) planted-defect prose.

Layout is deterministic: SectionMarker flowables record the page number of
every numbered section as it is drawn, so ground truth can cite pages
exactly. Each document is built in a single pass (BaseDocTemplate.build
truncates the output file, so one build per document is mandatory).
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (BaseDocTemplate, Frame, KeepTogether,
                                PageBreak, PageTemplate, Paragraph, Spacer,
                                Table, TableStyle)
from reportlab.platypus.flowables import Flowable

from backend.dataset import model as M

# ------------------------------------------------------------------ styles ---
_SS = getSampleStyleSheet()

H1 = ParagraphStyle("H1x", parent=_SS["Heading1"], fontName="Helvetica-Bold",
                    fontSize=16, spaceBefore=18, spaceAfter=10,
                    textColor=colors.HexColor("#0F2A43"))
H2 = ParagraphStyle("H2x", parent=_SS["Heading2"], fontName="Helvetica-Bold",
                    fontSize=12.5, spaceBefore=14, spaceAfter=6,
                    textColor=colors.HexColor("#1C4E80"))
H3 = ParagraphStyle("H3x", parent=_SS["Heading3"], fontName="Helvetica-Bold",
                    fontSize=11, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("BodyX", parent=_SS["BodyText"], fontName="Helvetica",
                      fontSize=9.5, leading=13.5, spaceAfter=6)
CELL = ParagraphStyle("CellX", parent=_SS["BodyText"], fontName="Helvetica",
                      fontSize=8.5, leading=11, spaceAfter=0)
CELLB = ParagraphStyle("CellBX", parent=CELL, fontName="Helvetica-Bold")
TITLE = ParagraphStyle("TitleX", parent=_SS["Title"], fontSize=20, leading=24,
                       alignment=TA_CENTER, spaceAfter=8)
SUBTITLE = ParagraphStyle("SubX", parent=_SS["Title"], fontSize=12, leading=16,
                          alignment=TA_CENTER, textColor=colors.grey)

GRID = TableStyle([
    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9DB2C6")),
    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1C4E80")),
    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, 0), 8.5),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("ROWBACKGROUNDS", (0, 1), (-1, -1),
     [colors.white, colors.HexColor("#EEF3F8")]),
    ("TOPPADDING", (0, 0), (-1, -1), 3),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
])


class SectionMarker(Flowable):
    """Zero-size flowable that records the page its section starts on."""

    def __init__(self, section_no: str, index: dict[str, int]) -> None:
        super().__init__()
        self.section_no = section_no
        self._index = index
        self.width = 0
        self.height = 0

    def draw(self) -> None:  # noqa: D102 - reportlab API
        page = self.canv.getPageNumber()
        if self.section_no not in self._index:
            self._index[self.section_no] = page


def _mark(section_no: str, index: dict[str, int]) -> SectionMarker:
    return SectionMarker(section_no, index)


def _bind_markers(story: list) -> list:
    """Glue each SectionMarker to its heading paragraph atomically.

    Note: reportlab may still split a zero-height leading flowable across a
    frame boundary, so the authoritative page index is measured from the
    RENDERED PDF (see ``index_from_rendered_pdf``); markers are a fallback.
    """
    out: list = []
    i = 0
    while i < len(story):
        f = story[i]
        if (isinstance(f, SectionMarker) and i + 1 < len(story)
                and isinstance(story[i + 1], Paragraph)):
            out.append(KeepTogether([f, story[i + 1]]))
            i += 2
        else:
            out.append(f)
            i += 1
    return out


def index_from_rendered_pdf(pdf_path: Path) -> dict[str, int]:
    """Measure {section_no: first_page} from the rendered PDF itself.

    Uses the same heading rules as the ingestion stage, so the ground-truth
    page index reflects exactly where headings are visible in the document.
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover
        import fitz as pymupdf  # type: ignore[no-redef]
    from backend.ingestion.sections import match_heading

    doc = pymupdf.open(str(pdf_path))
    index: dict[str, int] = {"0": 1}
    try:
        for pno in range(1, doc.page_count + 1):
            for ln in doc[pno - 1].get_text("text").splitlines():
                hit = match_heading(ln.strip())
                if hit is None:
                    continue
                no = hit[0]
                if no not in index:
                    index[no] = pno
    finally:
        doc.close()
    return index


def _on_page(canvas, doc):  # noqa: ANN001 - reportlab callback
    canvas.saveState()
    w, h = A4
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(colors.HexColor("#555555"))
    canvas.drawString(15 * mm, h - 10 * mm, M.DOC_TITLE)
    canvas.drawRightString(w - 15 * mm, h - 10 * mm,
                           f"{M.DOC_REF} | v{doc._as_version}")  # type: ignore
    canvas.setFont("Helvetica", 7.5)
    canvas.drawCentredString(w / 2, 9 * mm, f"Page {doc.page}")
    canvas.setFont("Helvetica", 6.5)
    canvas.drawString(15 * mm, 6 * mm, M.SECURITY_BANNER)
    canvas.drawRightString(w - 15 * mm, 6 * mm, M.DOC_ORG)
    canvas.restoreState()


def _build_template(path: Path, version: str) -> BaseDocTemplate:
    import reportlab.rl_config as rl_config

    rl_config.invariant = True  # deterministic PDF /ID + timestamps (D-015)
    doc = BaseDocTemplate(str(path), pagesize=A4,
                          leftMargin=15 * mm, rightMargin=15 * mm,
                          topMargin=18 * mm, bottomMargin=16 * mm,
                          title=M.DOC_TITLE, author=M.DOC_ORG,
                          invariant="D:20260101000000Z")
    doc._as_version = version  # type: ignore[attr-defined]
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  id="main")
    doc.addPageTemplates([PageTemplate(id="page", frames=[frame],
                                       onPage=_on_page)])
    return doc


def _table(headers: list[str], rows: list[list[str]],
           widths: list[float] | None = None) -> Table:
    data = [[Paragraph(h, CELLB) for h in headers]]
    for r in rows:
        data.append([Paragraph(str(c), CELL) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(GRID)
    return t


def _comp(cid: str) -> str:
    return f"{cid} ({next(c.name for c in M.COMPONENTS_V1 if c.id == cid)})"


def _comp_v2(cid: str, comps: list[M.SoTComponent],
             stale: bool = False) -> str:
    """Component name for v2; with ``stale`` keep the pre-rename name (D2)."""
    name = next(c.name for c in comps if c.id == cid)
    if stale:
        name = "VehicleModeSWC"
    return f"{cid} ({name})"


def _iface_name(iid: str) -> str:
    return next(i.name for i in M.INTERFACES_V1 if i.id == iid)


def _v1_ports() -> list[M.SoTPort]:
    ports: list[M.SoTPort] = []
    n = 0
    for i in M.INTERFACES_V1:
        n += 1
        ports.append(M.SoTPort(f"P-{n:03d}", i.provider, i.id, "provides"))
        for c in i.consumers:
            n += 1
            ports.append(M.SoTPort(f"P-{n:03d}", c, i.id, "requires"))
    return ports


def _v2_ports(v2: dict) -> list[M.SoTPort]:
    ports: list[M.SoTPort] = []
    n = 0
    for i in v2["interfaces"]:
        n += 1
        ports.append(M.SoTPort(f"P-{n:03d}", i.provider, i.id, "provides"))
        for c in i.consumers:
            n += 1
            ports.append(M.SoTPort(f"P-{n:03d}", c, i.id, "requires"))
    return ports


# ------------------------------------------------------------------ v1 -------


def render_v1(out_pdf: Path, index_out: Path | None = None) -> dict[str, int]:
    """Render HLD v1.0.0 and return {section_no: page}."""
    page_index: dict[str, int] = {}

    def M_(no: str) -> SectionMarker:  # local shorthand
        return _mark(no, page_index)

    doc = _build_template(out_pdf, "1.0.0")
    story: list = []

    # ---- Title page (section 0) ----
    story.append(M_("0"))
    story.append(Spacer(1, 60 * mm))
    story.append(Paragraph(M.DOC_TITLE, TITLE))
    story.append(Paragraph(M.DOC_SUBTITLE, SUBTITLE))
    story.append(Spacer(1, 30 * mm))
    story.append(_table(
        ["Field", "Value"],
        [["Document ID", M.DOC_REF + "-001"],
         ["Version", "1.0.0"],
         ["Status", "Released"],
         ["Organization", M.DOC_ORG],
         ["Security classification", "Synthetic sample"]],
    ))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "This document is a synthetic high-level design generated for an AI "
        "engineering-assistant demonstration. It does not describe any real "
        "vehicle program.", BODY))

    # ---- Section 1: Introduction ----
    story.append(PageBreak())
    story.append(M_("1"))
    story.append(Paragraph("1&nbsp;&nbsp;Introduction", H1))
    story.append(Paragraph("1.1&nbsp;&nbsp;Purpose", H3))
    story.append(Paragraph(
        "This High-Level Design (HLD) describes the software architecture of "
        "the Adaptive Body Controller (ABC) for the Body & Comfort domain. "
        "It defines software components, their ports and interfaces, "
        "communication signals, dependencies, and end-to-end functional "
        "flows. Section 3 defines the component architecture; Section 4 "
        "specifies interfaces; Section 5 lists the signal dictionary; "
        "Section 6 records dependencies; Section 7 describes functional "
        "flows.", BODY))
    story.append(Paragraph("1.2&nbsp;&nbsp;Scope", H3))
    story.append(Paragraph(
        "The document covers the application software components of the ABC "
        "ECU, the AUTOSAR BSW services they use, and the CAN communication "
        "path. Detailed timing analysis, AUTOSAR OS configuration, and ECU "
        "resource files are out of scope and are maintained separately.",
        BODY))
    story.append(Paragraph("1.3&nbsp;&nbsp;Revision History", H3))
    story.append(_table(
        ["Version", "Date", "Author", "Changes"],
        [[r["version"], r["date"], r["author"], r["changes"]]
         for r in M.REVISION_HISTORY[:1]]))
    story.append(Paragraph("1.4&nbsp;&nbsp;References", H3))
    story.append(Paragraph(
        "[R1] AUTOSAR Classic Platform Release R22-11 documentation.<br/>"
        "[R2] ABC System Requirements Specification (synthetic).<br/>"
        "[R3] Body & Comfort CAN Communication Matrix (synthetic).", BODY))
    story.append(Paragraph("1.5&nbsp;&nbsp;Terminology", H3))
    story.append(_table(
        ["Term", "Meaning"],
        [["SWC", "Software Component (AUTOSAR application component)"],
         ["BSW", "Basic Software"],
         ["S-R interface", "Sender-Receiver interface"],
         ["C-S interface", "Client-Server interface"],
         ["P-port", "Provided port (component provides the interface)"],
         ["R-port", "Required port (component requires the interface)"]]))

    # ---- Section 2: System Overview ----
    story.append(PageBreak())
    story.append(M_("2"))
    story.append(Paragraph("2&nbsp;&nbsp;System Overview", H1))
    story.append(Paragraph(
        "The ABC ECU implements body and comfort functions. Application SWCs "
        "implement feature logic, the RTE connects application ports to "
        "communication services, and the BSW stack transmits signals over "
        "CAN. The BodyControlSWC (C-08) is the central arbiter: application "
        "components submit requests to it, and it releases commands only "
        "when vehicle mode (C-09) and vehicle speed (C-10) interlocks "
        "permit.", BODY))
    story.append(Paragraph(
        "The layered view is: Application layer (C-01..C-10), RTE adapter "
        "(C-11), Services layer (C-12, C-13, C-14, C-16, C-17, C-18, C-19), "
        "and ECU abstraction (C-15, C-20).", BODY))
    story.append(Paragraph(
        "The communication path is: application SWC -> RteAdapter -> Com -> "
        "PduR -> CanIf -> CanDriver.", BODY))

    # ---- Section 3: Component Architecture ----
    story.append(PageBreak())
    story.append(M_("3"))
    story.append(Paragraph("3&nbsp;&nbsp;Component Architecture", H1))
    story.append(M_("3.1"))
    story.append(Paragraph("3.1&nbsp;&nbsp;Component Catalogue", H2))
    story.append(_table(
        ["ID", "Component", "Type", "Layer", "Description"],
        [[c.id, c.name, c.type, c.layer, c.description]
         for c in M.COMPONENTS_V1],
        widths=[12 * mm, 32 * mm, 20 * mm, 26 * mm, 85 * mm]))
    for idx, c in enumerate(M.COMPONENTS_V1, start=1):
        story.append(M_(f"3.2.{idx}"))
        if idx == 1:
            story.append(Paragraph("3.2&nbsp;&nbsp;Component Details", H2))
        story.append(Paragraph(f"3.2.{idx}&nbsp;&nbsp;Component {c.id}: "
                               f"{c.name}", H3))
        story.append(Paragraph(
            f"<b>{c.name}</b> ({c.type}, {c.layer} layer). "
            f"{c.description}", BODY))
        ports = [p for p in _v1_ports() if p.component_id == c.id]
        if ports:
            rows = []
            for p in ports:
                iface = next(i for i in M.INTERFACES_V1
                             if i.id == p.interface_id)
                rows.append([p.id, p.direction, iface.name, iface.kind])
            story.append(_table(["Port", "Direction", "Interface", "Kind"],
                                rows,
                                widths=[18 * mm, 26 * mm, 55 * mm, 22 * mm]))
        else:
            story.append(Paragraph(
                "This component has no explicitly assigned ports in this "
                "release; it communicates through its owning stack layer.",
                BODY))

    # ---- Section 4: Interface Specifications ----
    story.append(PageBreak())
    story.append(M_("4"))
    story.append(Paragraph("4&nbsp;&nbsp;Interface Specifications", H1))
    story.append(Paragraph(
        "Each interface below lists its provider (P-port owner) and its "
        "consumers (R-port owners). Signal definitions for each interface "
        "are given in Section 5.", BODY))
    for idx, i in enumerate(M.INTERFACES_V1, start=1):
        story.append(M_(f"4.{idx}"))
        story.append(Paragraph(f"4.{idx}&nbsp;&nbsp;{i.name} ({i.id})", H3))
        story.append(Paragraph(
            f"<b>{i.name}</b> is a {i.kind} interface. Provider: "
            f"{_comp(i.provider)}. Consumers: "
            f"{', '.join(_comp(c) for c in i.consumers)}.", BODY))
        sigs = [s for s in M.SIGNALS_V1 if s.interface_id == i.id]
        if sigs:
            story.append(_table(
                ["Signal", "Datatype", "Unit"],
                [[s.name, s.datatype, s.unit] for s in sigs],
                widths=[60 * mm, 40 * mm, 25 * mm]))

    # ---- Section 5: Signal Dictionary ----
    story.append(PageBreak())
    story.append(M_("5"))
    story.append(Paragraph("5&nbsp;&nbsp;Signal Dictionary", H1))
    story.append(Paragraph(
        "The signal dictionary lists all signals carried by the interfaces "
        "of Section 4, including datatype and physical unit. The "
        "VehicleSpeed signal (SG-018) is filtered by the SpeedProviderSWC "
        "before distribution.", BODY))
    story.append(_table(
        ["Signal ID", "Signal", "Datatype", "Unit", "Interface"],
        [[s.id, s.name, s.datatype, s.unit,
          _iface_name(s.interface_id)] for s in M.SIGNALS_V1],
        widths=[20 * mm, 48 * mm, 30 * mm, 20 * mm, 40 * mm]))

    # ---- Section 6: Dependencies ----
    story.append(PageBreak())
    story.append(M_("6"))
    story.append(Paragraph("6&nbsp;&nbsp;Dependencies", H1))
    story.append(M_("6.1"))
    story.append(Paragraph("6.1&nbsp;&nbsp;Dependency Overview", H2))
    story.append(Paragraph(
        "Dependencies record which components require services or commands "
        "from other components. Every dependency in this section is "
        "realized by an interface defined in Section 4.", BODY))
    story.append(_table(
        ["Dep. ID", "Source", "Target", "Type"],
        [[d.id, _comp(d.source_id), _comp(d.target_id), d.relationship]
         for d in M.DEPS_V1],
        widths=[20 * mm, 50 * mm, 50 * mm, 25 * mm]))
    for idx, d in enumerate(M.DEPS_V1, start=1):
        if idx == 1:
            story.append(Paragraph("6.2&nbsp;&nbsp;Dependency Details", H2))
        story.append(M_(f"6.2.{idx}"))
        story.append(Paragraph(f"6.2.{idx}&nbsp;&nbsp;{d.id}", H3))
        story.append(Paragraph(d.evidence, BODY))

    # ---- Section 7: Functional Flows ----
    story.append(PageBreak())
    story.append(M_("7"))
    story.append(Paragraph("7&nbsp;&nbsp;Functional Flows", H1))
    story.append(Paragraph(
        "Functional flows describe end-to-end behaviour across components. "
        "Flow steps reference the components of Section 3.", BODY))
    for idx, f in enumerate(M.FLOWS_V1, start=1):
        story.append(M_(f"7.{idx}"))
        story.append(Paragraph(f"7.{idx}&nbsp;&nbsp;{f.name} ({f.id})", H3))
        story.append(Paragraph(f"<b>Trigger:</b> {f.trigger}", BODY))
        story.append(Paragraph(
            "<b>Flow:</b> " + " &#8594; ".join(_comp(s) for s in f.steps),
            BODY))
        story.append(Paragraph(f.description, BODY))

    # ---- Section 8: Assumptions & Open Points ----
    story.append(PageBreak())
    story.append(M_("8"))
    story.append(Paragraph("8&nbsp;&nbsp;Assumptions and Open Points", H1))
    story.append(M_("8.1"))
    story.append(Paragraph("8.1&nbsp;&nbsp;Assumptions", H3))
    story.append(Paragraph(
        "A1. The CAN bus operates at 500 kbit/s.<br/>"
        "A2. The NvM stores up to three seat memory profiles per user.<br/>"
        "A3. Anti-pinch protection is mandatory for all windows when "
        "vehicle speed is below 5 km/h.", BODY))
    story.append(Paragraph("8.2&nbsp;&nbsp;Open Points", H3))
    story.append(Paragraph(
        "OP-1: Diagnostic read access for application data is handled via "
        "the DiagManager, which requires the RteAdapter for data access "
        "(see DEP-19).<br/>"
        "OP-2: Welcome-light sequencing depends on vehicle mode "
        "distribution (IF-12).", BODY))

    doc.build(_bind_markers(story))
    page_index = index_from_rendered_pdf(out_pdf) or page_index
    if index_out is not None:
        index_out.write_text(json.dumps(page_index, indent=2),
                             encoding="utf-8")
    return dict(page_index)


# ------------------------------------------------------------------ v2 -------

_DEFECT_PROSE = {
    "IF-11": ("SG-015 (KeyAuthStatus) was removed from this interface in "
              "revision 1.1.0; the section text above still describes the "
              "authentication status signal for legacy readers."),
    "IF-13": ("Note: the interface table lists BodyControlSWC as an "
              "additional provider of VehicleSpeedIF following the "
              "1.1.0 integration review; the descriptive text retains the "
              "original SpeedProviderSWC wording."),
}


def render_v2(out_pdf: Path, index_out: Path | None = None) -> dict[str, int]:
    """Render HLD v1.1.0 with planted defects and return {section_no: page}."""
    v2 = M.build_v2()
    comps = v2["components"]
    ifaces = v2["interfaces"]
    sigs = v2["signals"]
    deps = v2["dependencies"]
    flows = v2["flows"]

    page_index: dict[str, int] = {}

    def M_(no: str) -> SectionMarker:
        return _mark(no, page_index)

    doc = _build_template(out_pdf, "1.1.0")
    story: list = []

    story.append(M_("0"))
    story.append(Spacer(1, 60 * mm))
    story.append(Paragraph(M.DOC_TITLE, TITLE))
    story.append(Paragraph(M.DOC_SUBTITLE, SUBTITLE))
    story.append(Spacer(1, 30 * mm))
    story.append(_table(
        ["Field", "Value"],
        [["Document ID", M.DOC_REF + "-001"],
         ["Version", "1.1.0"],
         ["Status", "Released"],
         ["Organization", M.DOC_ORG],
         ["Security classification", "Synthetic sample"]],
    ))
    story.append(Spacer(1, 10 * mm))
    story.append(Paragraph(
        "Revision 1.1.0 of the synthetic Adaptive Body Controller HLD. This "
        "revision contains intentional inconsistencies for evaluation "
        "purposes.", BODY))

    # 1 Introduction
    story.append(PageBreak())
    story.append(M_("1"))
    story.append(Paragraph("1&nbsp;&nbsp;Introduction", H1))
    story.append(Paragraph("1.1&nbsp;&nbsp;Purpose", H3))
    story.append(Paragraph(
        "This High-Level Design (HLD) describes the software architecture "
        "of the Adaptive Body Controller (ABC). It defines software "
        "components, ports and interfaces, signals, dependencies, and "
        "functional flows. Section 3 defines the component architecture; "
        "Section 4 specifies interfaces; Section 5 lists the signal "
        "dictionary; Section 6 records dependencies; Section 7 describes "
        "functional flows.", BODY))
    story.append(Paragraph("1.2&nbsp;&nbsp;Scope", H3))
    story.append(Paragraph(
        "The document covers the application software components of the "
        "ABC ECU, the AUTOSAR BSW services they use, and the CAN "
        "communication path. Detailed timing analysis, OS configuration, "
        "and resource files are out of scope.", BODY))
    story.append(Paragraph("1.3&nbsp;&nbsp;Revision History", H3))
    story.append(_table(
        ["Version", "Date", "Author", "Changes"],
        [[r["version"], r["date"], r["author"], r["changes"]]
         for r in M.REVISION_HISTORY]))
    story.append(Paragraph("1.4&nbsp;&nbsp;References", H3))
    story.append(Paragraph(
        "[R1] AUTOSAR Classic Platform Release R22-11 documentation.<br/>"
        "[R2] ABC System Requirements Specification (synthetic).<br/>"
        "[R3] Body & Comfort CAN Communication Matrix (synthetic).", BODY))
    story.append(Paragraph("1.5&nbsp;&nbsp;Terminology", H3))
    story.append(_table(
        ["Term", "Meaning"],
        [["SWC", "Software Component (AUTOSAR application component)"],
         ["BSW", "Basic Software"],
         ["S-R interface", "Sender-Receiver interface"],
         ["C-S interface", "Client-Server interface"],
         ["P-port", "Provided port (component provides the interface)"],
         ["R-port", "Required port (component requires the interface)"]]))

    # 2 Overview
    story.append(PageBreak())
    story.append(M_("2"))
    story.append(Paragraph("2&nbsp;&nbsp;System Overview", H1))
    story.append(Paragraph(
        "The ABC ECU implements body and comfort functions. Application "
        "SWCs implement feature logic, the RTE connects application ports "
        "to communication services, and the BSW stack transmits signals "
        "over CAN. The BodyControlSWC (C-08) is the central arbiter: "
        "application components submit requests to it, and it releases "
        "commands only when vehicle mode (C-09) and vehicle speed (C-10) "
        "interlocks permit.", BODY))
    story.append(Paragraph(
        "The layered view is: Application layer (C-01..C-04, C-05..C-10), "
        "RTE adapter (C-11), Services layer (C-12, C-13, C-14, C-16, C-17, "
        "C-18, C-19), and ECU abstraction (C-15, C-20).", BODY))
    story.append(Paragraph(
        "The communication path is: application SWC -> RteAdapter -> Com -> "
        "PduR -> CanIf -> CanDriver.", BODY))

    # 3 Components
    story.append(PageBreak())
    story.append(M_("3"))
    story.append(Paragraph("3&nbsp;&nbsp;Component Architecture", H1))
    story.append(M_("3.1"))
    story.append(Paragraph("3.1&nbsp;&nbsp;Component Catalogue", H2))
    story.append(_table(
        ["ID", "Component", "Type", "Layer", "Description"],
        [[c.id, c.name, c.type, c.layer, c.description] for c in comps],
        widths=[12 * mm, 32 * mm, 20 * mm, 26 * mm, 85 * mm]))
    for idx, c in enumerate(comps, start=1):
        story.append(M_(f"3.2.{idx}"))
        if idx == 1:
            story.append(Paragraph("3.2&nbsp;&nbsp;Component Details", H2))
        story.append(Paragraph(f"3.2.{idx}&nbsp;&nbsp;Component {c.id}: "
                               f"{c.name}", H3))
        story.append(Paragraph(f"<b>{c.name}</b> ({c.type}, {c.layer} "
                               f"layer). {c.description}", BODY))
        ports = [p for p in _v2_ports(v2) if p.component_id == c.id]
        if ports:
            rows = []
            for p in ports:
                iface = next(i for i in ifaces if i.id == p.interface_id)
                rows.append([p.id, p.direction, iface.name, iface.kind])
            story.append(_table(["Port", "Direction", "Interface", "Kind"],
                                rows,
                                widths=[18 * mm, 26 * mm, 55 * mm, 22 * mm]))
        else:
            story.append(Paragraph(
                "This component has no explicitly assigned ports in this "
                "release; it communicates through its owning stack layer.",
                BODY))

    # 4 Interfaces
    story.append(PageBreak())
    story.append(M_("4"))
    story.append(Paragraph("4&nbsp;&nbsp;Interface Specifications", H1))
    story.append(Paragraph(
        "Each interface below lists its provider (P-port owner) and its "
        "consumers (R-port owners). Signal definitions for each interface "
        "are given in Section 5.", BODY))
    for idx, i in enumerate(ifaces, start=1):
        story.append(M_(f"4.{idx}"))
        story.append(Paragraph(f"4.{idx}&nbsp;&nbsp;{i.name} ({i.id})", H3))
        # D2: stale pre-rename component name in the IF-12 consumer cell
        provider_name = _comp_v2(i.provider, comps)
        consumers = ", ".join(_comp_v2(c, comps, stale=(i.id == "IF-12"))
                              for c in i.consumers)
        story.append(Paragraph(
            f"<b>{i.name}</b> is a {i.kind} interface. Provider: "
            f"{provider_name}. Consumers: {consumers}.", BODY))
        sig_rows = [[s.name, s.datatype, s.unit] for s in sigs
                    if s.interface_id == i.id]
        if sig_rows:
            story.append(_table(["Signal", "Datatype", "Unit"], sig_rows,
                                widths=[60 * mm, 40 * mm, 25 * mm]))
        if i.id in _DEFECT_PROSE:
            story.append(Paragraph(_DEFECT_PROSE[i.id], BODY))
        # D7: prose-vs-table port count mismatch on IF-04
        if i.id == "IF-04":
            story.append(Paragraph(
                "WindowCommandIF exposes two ports on the consuming side "
                "and one on the providing side (see table).", BODY))

    # 5 Signals
    story.append(PageBreak())
    story.append(M_("5"))
    story.append(Paragraph("5&nbsp;&nbsp;Signal Dictionary", H1))
    story.append(Paragraph(
        "The signal dictionary lists all signals carried by the interfaces "
        "of Section 4, including datatype and physical unit. The "
        "VehicleSpeed signal (SG-018) is filtered by the SpeedProviderSWC "
        "before distribution.", BODY))
    story.append(_table(
        ["Signal ID", "Signal", "Datatype", "Unit", "Interface"],
        [[s.id, s.name, s.datatype, s.unit, _iface_name(s.interface_id)]
         for s in sigs],
        widths=[20 * mm, 48 * mm, 30 * mm, 20 * mm, 40 * mm]))

    # 6 Dependencies
    story.append(PageBreak())
    story.append(M_("6"))
    story.append(Paragraph("6&nbsp;&nbsp;Dependencies", H1))
    story.append(M_("6.1"))
    story.append(Paragraph("6.1&nbsp;&nbsp;Dependency Overview", H2))
    story.append(Paragraph(
        "Dependencies record which components require services or commands "
        "from other components. Every dependency in this section is "
        "realized by an interface defined in Section 4.", BODY))
    story.append(_table(
        ["Dep. ID", "Source", "Target", "Type"],
        [[d.id, _comp_v2(d.source_id, comps),
          _comp_v2(d.target_id, comps), d.relationship] for d in deps],
        widths=[20 * mm, 50 * mm, 50 * mm, 25 * mm]))
    for idx, d in enumerate(deps, start=1):
        if idx == 1:
            story.append(Paragraph("6.2&nbsp;&nbsp;Dependency Details", H2))
        story.append(M_(f"6.2.{idx}"))
        story.append(Paragraph(f"6.2.{idx}&nbsp;&nbsp;{d.id}", H3))
        story.append(Paragraph(d.evidence, BODY))
        # D6: contradiction — DEP-05 remains listed while prose denies it
        if d.id == "DEP-05":
            story.append(Paragraph(
                "Note (1.1.0): ClimateInterfaceSWC does not require any "
                "interface from the BodyControlSWC; blower requests are "
                "issued autonomously.", BODY))

    # 7 Flows
    story.append(PageBreak())
    story.append(M_("7"))
    story.append(Paragraph("7&nbsp;&nbsp;Functional Flows", H1))
    story.append(Paragraph(
        "Functional flows describe end-to-end behaviour across components. "
        "Flow steps reference the components of Section 3.", BODY))
    for idx, f in enumerate(flows, start=1):
        story.append(M_(f"7.{idx}"))
        story.append(Paragraph(f"7.{idx}&nbsp;&nbsp;{f.name} ({f.id})", H3))
        story.append(Paragraph(f"<b>Trigger:</b> {f.trigger}", BODY))
        story.append(Paragraph(
            "<b>Flow:</b> " + " &#8594; ".join(
                _comp_v2(s, comps) for s in f.steps), BODY))
        story.append(Paragraph(f.description, BODY))

    # 8 Assumptions
    story.append(PageBreak())
    story.append(M_("8"))
    story.append(Paragraph("8&nbsp;&nbsp;Assumptions and Open Points", H1))
    story.append(M_("8.1"))
    story.append(Paragraph("8.1&nbsp;&nbsp;Assumptions", H3))
    story.append(Paragraph(
        "A1. The CAN bus operates at 500 kbit/s.<br/>"
        "A2. The NvM stores up to three seat memory profiles per user.<br/>"
        "A3. Anti-pinch protection is mandatory for all windows when "
        "vehicle speed is below 5 km/h.", BODY))
    story.append(Paragraph("8.2&nbsp;&nbsp;Open Points", H3))
    story.append(Paragraph(
        "OP-2: Welcome-light sequencing depends on vehicle mode "
        "distribution (IF-12). WindowStatusIF is additionally monitored by "
        "LightControlSWC since 1.1.0 (new consumer).", BODY))

    doc.build(_bind_markers(story))
    page_index = index_from_rendered_pdf(out_pdf) or page_index
    if index_out is not None:
        index_out.write_text(json.dumps(page_index, indent=2),
                             encoding="utf-8")
    return dict(page_index)
