"""Service facade re-exports (M8/M9): the only import surface pages need."""

from __future__ import annotations

from .app_services import (ServiceError,  # noqa: F401
                           analyze_findings, ask_copilot, compare_revisions,
                           filter_findings, findings_breakdown, get_copilot,
                           get_architecture_graph, get_architecture_stats,
                           get_document_summary, get_engine,
                           get_entity_detail, get_findings_from_db,
                           get_page_text, get_pdf_path,
                           get_versions, new_session, search_entities)
from .m9_services import (  # noqa: F401
    analyze_findings_profiled, ask_copilot_for_document, compare_gate,
    compare_revisions_gated, get_upload_copilot, get_upload_vector_store,
    get_versions_profiled, process_uploads, profile_of_version)
from .report_service import (build_report,  # noqa: F401
                             report_changes_to_csv, report_findings_to_csv,
                             report_impacts_to_csv, report_to_json)

__all__ = [
    "ServiceError",
    "analyze_findings", "ask_copilot", "compare_revisions", "filter_findings",
    "findings_breakdown", "get_architecture_graph", "get_architecture_stats",
    "get_copilot", "get_document_summary", "get_engine",
    "get_findings_from_db", "get_page_text", "get_pdf_path", "get_versions",
    "new_session", "search_entities",
    "analyze_findings_profiled", "ask_copilot_for_document", "compare_gate",
    "compare_revisions_gated", "get_upload_copilot",
    "get_upload_vector_store", "get_versions_profiled", "process_uploads",
    "profile_of_version",
    "build_report", "report_changes_to_csv", "report_findings_to_csv",
    "report_impacts_to_csv", "report_to_json",
]
