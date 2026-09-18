"""Copilot screen (M8.9/M8.10): grounded Q&A over the M3 pipeline.

The UI only collects question/version/params and renders the structured
``CopilotAnswer``; citation metadata comes from the M3 validator (trusted
retrieval metadata), never from the LLM. Provider/API-key problems surface
as actionable messages (M8.10) without exposing secrets or stack traces.
"""

from __future__ import annotations

import streamlit as st

from app import state
from app.services import ServiceError, ask_copilot, get_versions

_PROVIDERS = ["mock", "openrouter", "ollama"]
_PROVIDER_HELP = {
    "mock": "Deterministic offline provider (demo; no API key needed).",
    "openrouter": "Requires OPENROUTER_API_KEY configured in .env.",
    "ollama": "Requires a running local Ollama server.",
}


def render() -> None:
    try:
        versions = [v for v in get_versions() if v["has_chunks"]]
    except ServiceError as exc:
        st.error(f"Database unavailable: {exc}")
        return
    if not versions:
        st.warning(
            "No chunk index available. Run the M2 indexing pipeline first "
            "(scripts/build_index.py).")
        return

    labels = [f"{v['document_name']} — v{v['version']}" for v in versions]
    current = st.session_state.as_version
    idx = next((i for i, v in enumerate(versions)
                if v["version"] == current), 0)
    chosen = st.selectbox("Answer from", labels, index=idx)
    sel = versions[labels.index(chosen)]
    state.set_version(sel["version"])

    qcols = st.columns([5, 2, 1.4])
    with qcols[0]:
        question = st.text_input(
            "Question",
            value=st.session_state.as_question,
            placeholder="e.g. Which component provides the VehicleSpeed "
                        "interface?",
            key="cp_question")
    with qcols[1]:
        provider = st.selectbox(
            "LLM provider", _PROVIDERS,
            index=_PROVIDERS.index("mock"),
            help=_PROVIDER_HELP["openrouter"] + " " +
                 _PROVIDER_HELP["ollama"],
            key="cp_provider")
    with qcols[2]:
        top_k = st.number_input("top-k", 1, 10,
                                value=int(st.session_state.as_top_k),
                                key="cp_topk")
    st.session_state.as_top_k = int(top_k)

    if st.button("Ask", type="primary", key="cp_ask"):
        if not question.strip():
            st.warning("Enter a question first.")
        else:
            st.session_state.as_question = question
            with st.spinner("Retrieving evidence and generating answer…"):
                try:
                    st.session_state.as_last_answer = ask_copilot(
                        question.strip(), version=sel["version"],
                        top_k=int(top_k), provider=provider)
                except ServiceError as exc:
                    st.session_state.as_last_answer = None
                    st.error(_friendly_error(str(exc), provider))
                except Exception as exc:  # noqa: BLE001 - UI guard
                    st.session_state.as_last_answer = None
                    st.error(f"Copilot failed: {exc}")

    _render_answer(st.session_state.as_last_answer)


def _friendly_error(msg: str, provider: str) -> str:
    low = msg.lower()
    if "api key" in low or "openrouter_api_key" in low:
        return ("No OpenRouter API key configured. Set OPENROUTER_API_KEY "
                "in .env, or switch the provider to 'mock' / 'ollama'.")
    if "ollama" in low and ("connect" in low or "unavailable" in low
                            or "refused" in low):
        return ("Ollama is unreachable. Start it (e.g. `ollama serve`) and "
                "pull a model, or switch the provider to 'mock'.")
    if "dimension" in low or "index" in low:
        return ("The vector index does not match the configured embedding "
                "model. Rebuild the M2 index for this document.")
    return msg


def _render_answer(answer: dict | None) -> None:
    if answer is None:
        return
    status = answer.get("status")
    st.divider()
    if status == "answered":
        st.markdown("##### Answer")
        st.markdown(answer.get("answer", ""))
        llm = answer.get("llm", {})
        st.caption(f"provider: {llm.get('provider')} · "
                   f"model: {llm.get('model', '-')} · "
                   f"retrieval: {answer.get('retrieval', {}).get('mode')} "
                   f"({answer.get('retrieval', {}).get('n_chunks')} chunks) · "
                   f"{answer.get('timings_ms', {}).get('total_ms', 0):.0f} ms")
        _render_citations(answer.get("citations", []))
    elif status == "insufficient_evidence":
        st.warning(
            "**Insufficient evidence — no answer provided.**\n\n"
            f"{answer.get('detail', '')}")
        gate = answer.get("gate", {})
        if gate.get("missing_terms"):
            st.caption("Terms not found in evidence: "
                       + ", ".join(gate["missing_terms"][:8]))
    else:
        st.error(f"**{status}**: {answer.get('detail', '')}")
    issues = answer.get("issues", [])
    if issues:
        with st.expander("Citation validation issues"):
            for issue in issues:
                st.markdown(f"- `{issue.get('kind')}`: {issue.get('detail')}")


def _render_citations(citations: list[dict]) -> None:
    st.markdown("##### Sources")
    if not citations:
        st.caption("No citations attached to this answer.")
        return
    for c in citations:
        pages = c.get("pages") or "?"
        with st.expander(
                f"[{c['evidence_id']}] {c['document_name']} — "
                f"Section {c.get('section', '')} · pp.{pages}"):
            st.caption(f"chunk `{c.get('chunk_id', '')}` · "
                       f"version {c.get('version', '')}")
            st.markdown(f"> {c.get('quote', '')}")
