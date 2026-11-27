"""SQLite library: books, page-tagged chunks and a full-text index (FTS5/BM25).

Everything lives in one file under ``data/`` (git-ignored). Embeddings can be
added later as another table without changing callers.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

DATA_DIR = Path(os.environ.get("PSAI_DATA", "data"))
DB_PATH = DATA_DIR / "library.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
  id INTEGER PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  lang TEXT NOT NULL,
  title TEXT NOT NULL,
  source_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  n_pages INTEGER NOT NULL,
  n_ocr_pages INTEGER NOT NULL,
  n_low_quality INTEGER NOT NULL,
  ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chunks(
  id INTEGER PRIMARY KEY,
  book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
  ord INTEGER NOT NULL,
  page_from INTEGER NOT NULL,   -- printed page (or best estimate)
  page_to INTEGER NOT NULL,
  pdf_from INTEGER NOT NULL,
  pdf_to INTEGER NOT NULL,
  quality REAL NOT NULL,
  text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_book ON chunks(book_id, ord);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(norm, tokenize='unicode61 remove_diacritics 0');
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    return con


def delete_book(con: sqlite3.Connection, slug: str) -> None:
    row = con.execute("SELECT id FROM books WHERE slug=?", (slug,)).fetchone()
    if not row:
        return
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE book_id=?", (row[0],))]
    con.executemany("DELETE FROM chunks_fts WHERE rowid=?", [(i,) for i in ids])
    con.execute("DELETE FROM books WHERE id=?", (row[0],))
