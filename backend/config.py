"""
Central configuration for ArchSense.

Loads .env (if present) and resolves filesystem paths. Every module imports
paths/settings from here so nothing hardcodes absolute paths (Windows-safe).

M0/M1 note: no LLM or embedding configuration is *required* yet; those
settings are defined for M2+ and validated lazily.
"""

from __future__ import annotations

import os
from pathlib import Path

try:  # python-dotenv is optional; config must import without it
    from dotenv import load_dotenv

    # Repo root = parent of backend/
    _ROOT = Path(__file__).resolve().parent.parent
    load_dotenv(_ROOT / ".env")
except ImportError:  # pragma: no cover - python-dotenv optional
    _ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- paths ----
ROOT_DIR: Path = _ROOT
BACKEND_DIR: Path = ROOT_DIR / "backend"
DATA_DIR: Path = ROOT_DIR / os.getenv("DATA_DIR", "data")

SAMPLE_DOCS_DIR: Path = DATA_DIR / "sample_docs"    # generated HLD PDFs
GENERATED_DIR: Path = DATA_DIR / "generated"        # dataset build artifacts
GROUND_TRUTH_DIR: Path = DATA_DIR / "ground_truth"  # evaluation JSON
PROCESSED_DIR: Path = DATA_DIR / "processed"        # ingestion page records (M1)
DB_DIR: Path = DATA_DIR / "db"
DB_PATH: Path = DB_DIR / "archsense.db"
VECTORS_DIR: Path = DATA_DIR / "vectors"            # ChromaDB (M2)
GRAPHS_DIR: Path = DATA_DIR / "graphs"              # NetworkX JSON (M5)
EXPORTS_DIR: Path = DATA_DIR / "exports"            # reports (M8)
EVAL_DIR: Path = DATA_DIR / "evaluation"            # benchmark results (M2+)

DOCS_DIR: Path = ROOT_DIR / "docs"


def ensure_runtime_dirs() -> None:
    """Create runtime data directories (idempotent)."""
    for d in (
        DATA_DIR,
        SAMPLE_DOCS_DIR,
        GENERATED_DIR,
        GROUND_TRUTH_DIR,
        PROCESSED_DIR,
        DB_DIR,
        VECTORS_DIR,
        GRAPHS_DIR,
        EXPORTS_DIR,
        EVAL_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)


# ------------------------------------------------------------- LLM (M3+) ----
def _env_str(key: str, default: str) -> str:
    return os.getenv(key, default).strip()


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


OPENROUTER_API_KEY: str = _env_str("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL: str = _env_str("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_MODEL: str = _env_str("OPENROUTER_MODEL", "google/gemma-3-27b-it:free")

OLLAMA_BASE_URL: str = _env_str("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL: str = _env_str("OLLAMA_MODEL", "llama3.1:8b")

LLM_TIMEOUT_SECONDS: int = int(_env_str("LLM_TIMEOUT_SECONDS", "90"))
LLM_MAX_RETRIES: int = int(_env_str("LLM_MAX_RETRIES", "2"))

# ------------------------------------------------------------ M2+ knobs ----
EMBEDDING_MODEL: str = _env_str("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_MODEL_CANDIDATES: list[str] = [
    m.strip()
    for m in _env_str(
        "EMBEDDING_MODEL_CANDIDATES",
        "all-MiniLM-L6-v2,BAAI/bge-small-en-v1.5,intfloat/e5-small-v2,BAAI/bge-m3",
    ).split(",")
    if m.strip()
]
CHUNK_TOKEN_TARGET: int = int(_env_str("CHUNK_TOKEN_TARGET", "500"))
CHUNK_OVERLAP_RATIO: float = float(_env_str("CHUNK_OVERLAP_RATIO", "0.15"))

# ------------------------------------------------------------ M3 knobs ----
# Retrieval mode: "hybrid" (default, BM25 + dense fused with RRF), "dense",
# or "lexical" (kept for testing/comparison).
RETRIEVAL_MODE: str = _env_str("RETRIEVAL_MODE", "hybrid")
HYBRID_LEXICAL_K: int = int(_env_str("HYBRID_LEXICAL_K", "20"))
HYBRID_DENSE_K: int = int(_env_str("HYBRID_DENSE_K", "20"))
HYBRID_RRF_K: int = int(_env_str("HYBRID_RRF_K", "60"))

# Evidence gate (M3.11): retrieval-quality refusal policy, calibrated on
# the 30 answerable + 4 unanswerable GT questions (D-019, one-shot grid in
# data/evaluation/gate_calibration.json): mechanically refuses QA-U1 (zero
# lexical match) and QA-U2 (low query coverage), 1 false refusal (QA-21);
# QA-U3/U4 are topically adjacent and pass the gate by design -- they are
# the LLM structured-refusal layer's job (defense in depth, not perfection).
EVIDENCE_MIN_SCORE: float = float(_env_str("EVIDENCE_MIN_SCORE", "0.10"))
EVIDENCE_MIN_HITS: int = int(_env_str("EVIDENCE_MIN_HITS", "1"))
EVIDENCE_MIN_AGREEMENT: float = float(_env_str("EVIDENCE_MIN_AGREEMENT", "0.0"))
EVIDENCE_MIN_LEXICAL_SCORE: float = float(_env_str("EVIDENCE_MIN_LEXICAL_SCORE", "0.25"))
EVIDENCE_MIN_COVERAGE: float = float(_env_str("EVIDENCE_MIN_COVERAGE", "0.30"))

# Answer max tokens passed to providers (all providers cap this).
LLM_MAX_TOKENS: int = int(_env_str("LLM_MAX_TOKENS", "700"))

# Which provider the copilot uses: "openrouter" | "ollama" | "mock".
# Default is deliberately "mock" so the repo is runnable with zero
# credentials; set to "openrouter" (with OPENROUTER_API_KEY in .env) for
# real generation.
LLM_PROVIDER: str = _env_str("LLM_PROVIDER", "mock")

# --------------------------------------------------------- M4 knobs ----
# Extraction: deterministic pass always runs; the LLM pass is opt-in
# (mock provider by default, so zero credentials are needed).
EXTRACTION_USE_LLM: bool = _env_bool("EXTRACTION_USE_LLM", False)
EXTRACTION_MIN_CONFIDENCE: float = float(
    _env_str("EXTRACTION_MIN_CONFIDENCE", "0.5"))
EXTRACTION_MAX_CHUNKS: int = int(_env_str("EXTRACTION_MAX_CHUNKS", "400"))
EXTRACTION_LLM_PROVIDER: str = _env_str("EXTRACTION_LLM_PROVIDER", "mock")

# --------------------------------------------------------- governance ------
AUDIT_LOG_ENABLED: bool = _env_bool("AUDIT_LOG_ENABLED", True)
HUMAN_REVIEW_REQUIRED: bool = _env_bool("HUMAN_REVIEW_REQUIRED", True)
APP_USER: str = _env_str("APP_USER", "demo_user")
