"""ArchSense Streamlit application (M8).

A thin UI over the M1-M7 backend subsystems. All analysis logic stays in
``backend/`` — the app layers only state (``state.py``), cached service
adapters (``services/``) and screens (``pages/``) on top. See D-041.
"""
