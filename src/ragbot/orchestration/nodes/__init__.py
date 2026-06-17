"""Reusable graph node helpers (Adaptive Router L1/L3 + future extractions).

The legacy graph kept every node inline in ``query_graph.py`` (~4k lines).
New work-stream pure helpers live here so the orchestrator imports them
without bloating the megafile. Each module MUST stay domain-neutral and
swap-able (Strategy + DI) so the same helper is reused across bots.
"""
