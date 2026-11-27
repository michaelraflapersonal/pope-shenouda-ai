# Ask Pope Shenouda (AI) — private MVP

A private Arabic Q&A that answers from Pope Shenouda III's books, quoting them with book and page.
It is an AI built on his books: **not Pope Shenouda, and not the Church.** Every answer says so.

**This repo holds code only.** The books and everything derived from them (page text, index) are
copyrighted, stay out of git (`data/`, `*.pdf`, `*.sqlite` are ignored) and must not be committed.

## How it works

```
PDFs ──extract──► page text ──chunk──► SQLite (FTS5/BM25) ──retrieve──► Claude ──verify quotes──► answer
        │ repair Arabic text layer         ~260 words, page-tagged      top passages   answers only from them
        └ OCR when there is none           stemmed Arabic index         (RRF of queries)  quotes checked word for word
```

* `psai/arabic.py` – normalisation: presentation forms → letters, look-alike letters, `االله`→`الله`,
  search folding (alef/yeh/teh-marbuta/diacritics) and light stemming; a page-quality score.
* `psai/extract.py` – per page: rebuild the text layer from glyph positions (fixes the reversed, split
  old encoding), score it, and fall back to OCR (Tesseract `ara`, or `--ocr claude`) when it is unreadable.
  Most Arabic books are image scans, so OCR is the common path (about 0.3 s/page on 4 cores).
* `psai/ingest.py` – **re-runnable**. A book is reprocessed only when its PDF hash changes; page text is
  cached in `data/pages/`, so adding the three missing books later is just dropping the PDFs in and re-running.
* `psai/search.py` – BM25 over the stemmed index, several queries merged by reciprocal-rank fusion.
* `psai/answer.py` – Claude gets only the retrieved passages. Guardrails: nothing found → fixed
  "he did not address this" reply; every quotation is verified word for word against the cited passage
  and dropped if not; an answer with no verifiable quote is withheld; never first person; priest/crisis pointers.
* `psai/server.py` + `psai/web/index.html` – RTL Arabic page with the AI notice, quotes with citations,
  expandable source passages, optional English rendering (labelled machine translation).

## Run it

```bash
pip install -r requirements.txt
sudo apt install tesseract-ocr tesseract-ocr-ara        # OCR for scanned books

# 1. build the private library (re-run any time; unchanged books are skipped)
python -m psai.ingest --pdf-dir /path/to/pdfs --lang ar           # add --only "Anger" to test one book

# 2. ask
export ANTHROPIC_API_KEY=...            # required
export PSAI_ACCESS_CODE=choose-a-code   # optional: require a code in the page
python -m psai.cli "كيف أتعامل مع الغضب؟"
uvicorn psai.server:app --host 127.0.0.1 --port 8000
```

`PSAI_DATA` (default `./data`) sets where the library lives; `PSAI_MODEL` (default `claude-opus-5-5`) the model.
Tests: `python -m pytest tests` (no API key or books needed).

## Not built yet (designed for)

* **Voice / avatar:** `ask()` returns a plain, already-verified result (`answer_ar`, `quotes`); a later
  voice or video step should consume only that, never free text.
* **Embeddings:** add a vector table beside `chunks` and fuse it in `search()`; today retrieval is keyword-only.
* **Gates before any public use:** rights holder licence, Church liaison, clergy-reviewed test set (200–300 questions),
  and a "report this answer" flow.
* OCR is raw Tesseract; pages with a low score are flagged in `books.n_low_quality` for a Claude-vision re-OCR
  (`--ocr claude --force`) or human proofreading. Scripture references are the weakest part of OCR output.
