"""Keyword retrieval over the library (BM25 via SQLite FTS5)."""

from __future__ import annotations

from dataclasses import dataclass

from .arabic import normalize_search, tokenize
from .store import connect


@dataclass
class Passage:
    id: int
    book: str
    lang: str
    page_from: int
    page_to: int
    text: str
    score: float

    @property
    def cite(self) -> str:
        pg = str(self.page_from) if self.page_from == self.page_to else f"{self.page_from}-{self.page_to}"
        return f"{self.book}، ص {pg}" if self.lang == "ar" else f"{self.book}, p. {pg}"


def _fts_query(text: str) -> str:
    toks = [t for t in dict.fromkeys(normalize_search(text).split()) if len(t) > 1]
    return " OR ".join(f'"{t}"' for t in toks)


def search(queries: list[str] | str, k: int = 8, lang: str = "ar", con=None) -> list[Passage]:
    """Run one or more query strings, merge with reciprocal-rank fusion, return top k."""
    con = con or connect()
    if isinstance(queries, str):
        queries = [queries]
    fused: dict[int, float] = {}
    for q in queries:
        fq = _fts_query(q)
        if not fq:
            continue
        rows = con.execute(
            "SELECT chunks_fts.rowid AS id FROM chunks_fts "
            "JOIN chunks c ON c.id = chunks_fts.rowid JOIN books b ON b.id = c.book_id "
            "WHERE chunks_fts MATCH ? AND b.lang = ? AND c.quality >= 0.3 "
            "ORDER BY bm25(chunks_fts) LIMIT 40", (fq, lang)).fetchall()
        for rank, r in enumerate(rows):
            fused[r["id"]] = fused.get(r["id"], 0.0) + 1.0 / (60 + rank)
    top = sorted(fused.items(), key=lambda kv: -kv[1])[:k]
    out = []
    for cid, score in top:
        r = con.execute(
            "SELECT c.*, b.title, b.lang FROM chunks c JOIN books b ON b.id=c.book_id WHERE c.id=?", (cid,)
        ).fetchone()
        out.append(Passage(cid, r["title"], r["lang"], r["page_from"], r["page_to"], r["text"], score))
    return out


def coverage(question_terms: list[str], p: Passage) -> float:
    """Share of the query's content words that appear in the passage."""
    want = set(normalize_search(" ".join(question_terms)).split())
    have = set(normalize_search(p.text).split())
    return len(want & have) / len(want) if want else 0.0
