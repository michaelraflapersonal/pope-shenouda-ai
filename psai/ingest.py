"""Re-runnable ingestion: PDFs -> page text cache -> chunks -> SQLite index.

Safe to run again whenever books are added or replaced: a book is re-processed
only when its PDF content hash changes (or with ``--force``). Page text is
cached in ``data/pages/<slug>.json`` so re-chunking never needs OCR again.

    python -m psai.ingest --pdf-dir /mnt/project-files/pope-shenouda/pdfs \\
        [--lang ar] [--only "Anger"] [--limit 5] [--ocr tesseract|claude] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
import time
from pathlib import Path

from .arabic import normalize_search
from .extract import LOW_QUALITY, extract_pdf, reocr_pages
from .store import DATA_DIR, connect, delete_book

CHUNK_WORDS = 260
OVERLAP_WORDS = 40


def slugify(lang: str, title: str) -> str:
    return f"{lang}-" + re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cite_pages(pages: list[dict]) -> list[int]:
    """Printed page per PDF page; fill gaps using the book's usual offset."""
    offsets = [p["printed_page"] - p["pdf_page"] for p in pages if p["printed_page"]]
    offset = statistics.mode(offsets) if offsets else 0
    out = []
    for p in pages:
        expected = max(1, p["pdf_page"] + offset)
        # OCR misreads page numbers; trust a printed number only if it fits the book's numbering.
        ok = p["printed_page"] and abs(p["printed_page"] - expected) <= 1
        out.append(p["printed_page"] if ok else expected)
    return out


def chunk_pages(pages: list[dict]) -> list[dict]:
    """Greedy ~260-word chunks along line breaks, with overlap, tracking page range."""
    cites = cite_pages(pages)
    units = []  # (word_count, text, page_idx)
    for idx, p in enumerate(pages):
        for line in p["text"].split("\n"):
            line = line.strip()
            if line:
                units.append((len(line.split()), line, idx))
    chunks, cur, cur_words = [], [], 0

    def flush(final=False):
        nonlocal cur, cur_words
        if not cur:
            return
        idxs = [u[2] for u in cur]
        q = [pages[i]["quality"] for i in set(idxs)]
        chunks.append({
            "text": "\n".join(u[1] for u in cur),
            "page_from": min(cites[i] for i in idxs), "page_to": max(cites[i] for i in idxs),
            "pdf_from": pages[min(idxs)]["pdf_page"], "pdf_to": pages[max(idxs)]["pdf_page"],
            "quality": round(sum(q) / len(q), 3),
        })
        if final:
            cur, cur_words = [], 0
            return
        keep, w = [], 0
        for u in reversed(cur):
            if w >= OVERLAP_WORDS:
                break
            keep.insert(0, u)
            w += u[0]
        cur, cur_words = keep, w

    for u in units:
        cur.append(u)
        cur_words += u[0]
        if cur_words >= CHUNK_WORDS:
            flush()
    if cur and (not chunks or sum(u[0] for u in cur) > OVERLAP_WORDS + 10):
        flush(final=True)
    return [c for c in chunks if len(c["text"].split()) >= 8]


def ingest_book(con, path: Path, lang: str, ocr: str, force: bool, workers: int | None,
                rechunk: bool = False, reocr_below: float = 0.0) -> str:
    title = path.stem
    slug = slugify(lang, title)
    digest = sha256(path)
    row = con.execute("SELECT sha256 FROM books WHERE slug=?", (slug,)).fetchone()
    if row and row["sha256"] == digest and not force and not rechunk and not reocr_below:
        return "unchanged"

    cache = DATA_DIR / "pages" / f"{slug}.json"
    pages = None
    if cache.exists() and not force:
        c = json.loads(cache.read_text())
        if c.get("sha256") == digest:
            pages = c["pages"]
    if pages is not None and reocr_below:
        n = reocr_pages(str(path), pages, reocr_below, ocr, lang, workers)
        if n:
            cache.write_text(json.dumps({"sha256": digest, "pages": pages}, ensure_ascii=False))
        rechunk = True
    if pages is None:
        extracted = extract_pdf(str(path), lang=lang, ocr_engine=ocr, workers=workers)
        pages = [p.__dict__ for p in extracted]
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"sha256": digest, "pages": pages}, ensure_ascii=False))

    chunks = chunk_pages(pages)
    delete_book(con, slug)
    n_ocr = sum(1 for p in pages if p["method"].startswith("ocr"))
    n_low = sum(1 for p in pages if p["quality"] < LOW_QUALITY)
    cur = con.execute(
        "INSERT INTO books(slug,lang,title,source_path,sha256,n_pages,n_ocr_pages,n_low_quality)"
        " VALUES(?,?,?,?,?,?,?,?)", (slug, lang, title, str(path.name), digest, len(pages), n_ocr, n_low))
    book_id = cur.lastrowid
    for i, c in enumerate(chunks):
        cid = con.execute(
            "INSERT INTO chunks(book_id,ord,page_from,page_to,pdf_from,pdf_to,quality,text)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (book_id, i, c["page_from"], c["page_to"], c["pdf_from"], c["pdf_to"], c["quality"], c["text"]),
        ).lastrowid
        con.execute("INSERT INTO chunks_fts(rowid,norm) VALUES(?,?)", (cid, normalize_search(c["text"])))
    con.commit()
    return f"{len(pages)} pages ({n_ocr} OCR, {n_low} low quality), {len(chunks)} chunks"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pdf-dir", required=True, help="folder holding arabic/ and english/ subfolders")
    ap.add_argument("--lang", choices=["ar", "en", "both"], default="ar")
    ap.add_argument("--only", help="substring of the title to process")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--ocr", choices=["tesseract", "documentai", "claude"], default="tesseract")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--force", action="store_true", help="redo extraction/OCR too")
    ap.add_argument("--reocr-below", type=float, default=0.0, metavar="Q",
                    help="re-OCR cached pages scoring below Q (e.g. 0.6) with --ocr engine, then rebuild")
    ap.add_argument("--rechunk", action="store_true", help="rebuild chunks and index from cached page text")
    a = ap.parse_args(argv)

    con = connect()
    langs = ["ar", "en"] if a.lang == "both" else [a.lang]
    done = 0
    for lang in langs:
        folder = Path(a.pdf_dir) / ("arabic" if lang == "ar" else "english")
        for path in sorted(folder.glob("*.pdf")):
            if a.only and a.only.lower() not in path.stem.lower():
                continue
            if a.limit and done >= a.limit:
                break
            t = time.time()
            try:
                msg = ingest_book(con, path, lang, a.ocr, a.force, a.workers, a.rechunk, a.reocr_below)
            except Exception as exc:
                msg = f"FAILED: {exc}"
            done += 1
            print(f"[{lang}] {path.stem}: {msg} ({time.time()-t:.0f}s)", flush=True)
    n = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
    print(f"library now holds {n} books")


if __name__ == "__main__":
    sys.exit(main())
