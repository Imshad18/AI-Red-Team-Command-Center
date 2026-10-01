from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .analyzer import STOPWORDS, detect_techniques, tokenize


def _cosine(a: dict[str, int], b: dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[k] * b[k] for k in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def similar_documents(db, query: str, limit: int = 10) -> list[dict[str, Any]]:
    qtokens = [t for t in tokenize(query) if t not in STOPWORDS]
    qcounts = dict(Counter(qtokens).most_common(220))
    top_terms = [t for t, _ in Counter(qtokens).most_common(18)]
    ids = db.fts_candidates(top_terms, limit=140)
    docs = db.all_term_documents(ids=ids if ids else None, limit=1200)
    query_techs = set(detect_techniques(query))
    results = []
    for doc in docs:
        score = _cosine(qcounts, doc.get("token_counts") or {})
        doc_techs = set(doc.get("techniques") or [])
        if query_techs or doc_techs:
            overlap = len(query_techs & doc_techs)
            union = len(query_techs | doc_techs) or 1
            score += 0.28 * (overlap / union)
        if doc.get("outcome") == "success":
            score += 0.035
        elif doc.get("outcome") == "partial":
            score += 0.015
        if score <= 0:
            continue
        item = dict(doc)
        item["similarity"] = round(min(1.0, score) * 100, 1)
        results.append(item)
    results.sort(key=lambda x: x["similarity"], reverse=True)
    return results[:limit]


def search_documents(db, query: str, limit: int = 40) -> list[dict[str, Any]]:
    return similar_documents(db, query, limit=limit)
