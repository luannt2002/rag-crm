"""MMR (Maximal Marginal Relevance) — diversity filter for retrieved chunks.

Selects chunks greedily by maximising:
    mmr_score = lambda * relevance - (1 - lambda) * max_similarity_to_selected

Two similarity backends:

- Embedding cosine (preferred) — when chunks carry an ``embedding`` field
  (``list[float]``) the filter computes vector cosine similarity. Captures
  semantic diversity (paraphrases, synonym swaps). Vectorised with NumPy:
  candidate matrix is L2-normalised once, then ``max_sim_to_selected`` is
  maintained as a running ``(n,)`` array updated by a single matrix-vector
  product per pick — O(n·d) per pick instead of O(n·k·d).
- Character trigram Jaccard (fallback) — used when ``embedding`` is absent
  or ``use_cosine=False``. Lexical only.

Complexity: O(n*k) with n = candidates, k = selected. OK for k small.
"""

from __future__ import annotations

import math
from typing import Sequence

import numpy as np

from ragbot.shared.constants import DEFAULT_MMR_USE_COSINE


def _trigram_similarity(text_a: str, text_b: str) -> float:
    """Jaccard similarity on character trigrams between two strings."""
    if not text_a or not text_b:
        return 0.0
    trigrams_a = {text_a[i : i + 3] for i in range(len(text_a) - 2)}
    trigrams_b = {text_b[i : i + 3] for i in range(len(text_b) - 2)}
    if not trigrams_a or not trigrams_b:
        return 0.0
    intersection = len(trigrams_a & trigrams_b)
    union = len(trigrams_a | trigrams_b)
    return intersection / union if union > 0 else 0.0


def _cosine_similarity(vec_a: Sequence[float], vec_b: Sequence[float]) -> float:
    """Cosine similarity between two equal-length numeric vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for a, b in zip(vec_a, vec_b):
        dot += a * b
        norm_a += a * a
        norm_b += b * b
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def _extract_embedding(chunk: dict) -> list[float] | None:
    """Return a normalised list[float] embedding for *chunk*, or ``None``."""
    emb = chunk.get("embedding")
    if emb is None:
        return None
    if isinstance(emb, (list, tuple)):
        try:
            return [float(x) for x in emb]
        except (TypeError, ValueError):
            return None
    return None


def mmr_filter(
    chunks: list[dict],
    *,
    lambda_param: float = 0.7,
    similarity_threshold: float = 0.88,
    max_results: int | None = None,
    use_cosine: bool = DEFAULT_MMR_USE_COSINE,
    strip_embedding: bool = False,
) -> list[dict]:
    """MMR diversity filter — cosine when embeddings present, trigram fallback.

    @param chunks: list of dicts with 'content'/'text', optionally 'score'/'rerank_score'
        and 'embedding' (``list[float]``).
    @param lambda_param: 1.0 = pure relevance order, 0.0 = maximum diversity.
    @param similarity_threshold: hard ceiling — any candidate more similar than
        this to an already-selected chunk is rejected regardless of score.
        Interpreted in the active similarity space (cosine 0..1 or Jaccard 0..1).
    @param max_results: optional cap on returned chunks (None = no cap).
    @param use_cosine: when True and chunks have ``embedding`` field, use cosine;
        else fall back to character trigram Jaccard.
    @param strip_embedding: pop ``embedding`` from each output chunk before
        returning. Lets pipeline callers release the per-chunk vector once
        cosine diversity has consumed it; downstream nodes (grade /
        generate / persist) only need ``content`` and never read the
        embedding back. Mutates the chunk dicts in-place.
    @return: filtered chunks ordered by MMR score
    """
    lambda_param = max(0.0, min(1.0, lambda_param))

    if len(chunks) <= 1:
        out_short = chunks[:max_results] if max_results else list(chunks)
        if strip_embedding:
            for c in out_short:
                c.pop("embedding", None)
        return out_short

    # Decide algorithm — cosine requires every selected chunk to expose an
    # embedding. If even one is missing we fall back to trigram for the
    # whole batch to keep the comparison space consistent.
    embeddings: list[list[float] | None] = [_extract_embedding(c) for c in chunks]
    cosine_active = bool(use_cosine) and all(e is not None for e in embeddings)

    # Pre-stack and L2-normalise the candidate embedding matrix once. Cosine
    # similarity between unit vectors is just their dot product, so each pick
    # only needs a single (n,d)·(d,) matrix-vector product instead of k loops
    # over d-length Python lists. ``max_sim_arr`` is the running per-candidate
    # max similarity to the selected set — updated in-place after each pick.
    emb_matrix: np.ndarray | None = None
    max_sim_arr: np.ndarray | None = None
    if cosine_active:
        # ``embeddings`` is fully populated when cosine_active is True (checked
        # above) — every entry is a list[float] of equal length.
        try:
            emb_matrix = np.asarray(embeddings, dtype=np.float64)
        except (TypeError, ValueError):
            # Ragged input (mismatched dims) → fall back to trigram path.
            cosine_active = False
        if cosine_active and emb_matrix is not None:
            if emb_matrix.ndim != 2 or emb_matrix.shape[1] == 0:  # noqa: PLR2004 — ndim==2 is the literal "2-D matrix" shape, not a tunable.
                cosine_active = False
            else:
                norms = np.linalg.norm(emb_matrix, axis=1, keepdims=True)
                # Zero-norm rows degrade to all-zero similarity (matches the
                # pure-Python helper which returns 0.0 when norm_a or norm_b
                # is 0). Avoid divide-by-zero by replacing 0 norms with 1
                # before division — the row stays all-zero either way.
                safe_norms = np.where(norms > 0.0, norms, 1.0)
                emb_matrix = emb_matrix / safe_norms
                emb_matrix[norms[:, 0] <= 0.0] = 0.0
                max_sim_arr = np.zeros(emb_matrix.shape[0], dtype=np.float64)

    selected: list[dict] = [chunks[0]]
    selected_texts: list[str] = [
        (chunks[0].get("content") or chunks[0].get("text") or "").lower()
    ]

    # Seed the running max-similarity array with the first selected chunk.
    if cosine_active and emb_matrix is not None and max_sim_arr is not None:
        sims0 = emb_matrix @ emb_matrix[0]
        np.maximum(max_sim_arr, sims0, out=max_sim_arr)

    candidates = list(enumerate(chunks))[1:]

    while candidates:
        if max_results and len(selected) >= max_results:
            break

        best_pos = -1
        best_mmr_score = float("-inf")

        for pos, (orig_idx, candidate) in enumerate(candidates):
            cand_text = (candidate.get("content") or candidate.get("text") or "").lower()

            relevance_score = float(
                candidate.get("score", candidate.get("rerank_score", 0)) or 0
            )

            if cosine_active and max_sim_arr is not None:
                max_sim_to_selected = float(max_sim_arr[orig_idx])
            else:
                max_sim_to_selected = 0.0
                if cand_text:
                    for sel_text in selected_texts:
                        if not sel_text:
                            continue
                        sim = _trigram_similarity(cand_text, sel_text)
                        if sim > max_sim_to_selected:
                            max_sim_to_selected = sim

            if max_sim_to_selected > similarity_threshold:
                continue

            mmr_score = (
                lambda_param * relevance_score
                - (1 - lambda_param) * max_sim_to_selected
            )

            if mmr_score > best_mmr_score:
                best_mmr_score = mmr_score
                best_pos = pos

        if best_pos < 0:
            break  # all remaining candidates exceed similarity threshold

        orig_idx, winner = candidates.pop(best_pos)
        selected.append(winner)
        selected_texts.append(
            (winner.get("content") or winner.get("text") or "").lower()
        )
        if cosine_active and emb_matrix is not None and max_sim_arr is not None:
            sims = emb_matrix @ emb_matrix[orig_idx]
            np.maximum(max_sim_arr, sims, out=max_sim_arr)

    if strip_embedding:
        for c in selected:
            c.pop("embedding", None)
    return selected


def mmr_algorithm(chunks: list[dict], *, use_cosine: bool = DEFAULT_MMR_USE_COSINE) -> str:
    """Return the algorithm tag (``cosine`` | ``trigram``) for *chunks* under *use_cosine*.

    Mirrors the decision inside ``mmr_filter`` so callers can audit which
    similarity backend was actually used without re-running the filter.
    """
    if not use_cosine or not chunks:
        return "trigram"
    return "cosine" if all(_extract_embedding(c) is not None for c in chunks) else "trigram"
