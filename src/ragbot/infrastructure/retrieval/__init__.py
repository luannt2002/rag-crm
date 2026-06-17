"""Lexical retrieval strategies — Port + Adapter + Null + Registry.

The package mirrors the reranker / embedding layout so adding a new
lexical backend (e.g. Elasticsearch, OpenSearch, in-process BM25) is a
single file + one registry entry. The orchestrator never imports a
concrete adapter; it talks to ``LexicalRetrievalPort`` only.
"""
