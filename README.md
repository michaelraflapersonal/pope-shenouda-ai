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
* `psai/llm.py` – one JSON-generation call over Claude on Vertex, Gemini on Vertex, or the Anthropic API.
* `psai/answer.py` – the model gets only the retrieved passages. Guardrails: nothing found → fixed
  "he did not address this" reply; every quotation is verified word for word against the cited passage
  and dropped if not; an answer with no verifiable quote is withheld; never first person; priest/crisis pointers.
* `psai/server.py` + `psai/web/index.html` – RTL Arabic page with the AI notice, quotes with citations,
  expandable source passages, optional English rendering (labelled machine translation).

## Run it locally

```bash
pip install -r requirements-ingest.txt
sudo apt install tesseract-ocr tesseract-ocr-ara        # free local OCR for scanned books

# 1. build the private library (re-run any time; unchanged books are skipped)
python -m psai.ingest --pdf-dir /path/to/pdfs --lang ar           # add --only "Anger" to test one book

# 2. ask (pick a model provider, see psai/llm.py)
export ANTHROPIC_API_KEY=...            # local: Claude via the Anthropic API
python -m psai.cli "كيف أتعامل مع الغضب؟"
export PSAI_ACCESS_CODE=choose-a-code   # optional: require a code in the page
uvicorn psai.server:app --host 127.0.0.1 --port 8000
```

`PSAI_DATA` (default `./data`) is where the library lives. Tests: `python -m pytest tests` (no keys or books needed).

## Google Cloud (chosen platform)

| Piece | Service |
|---|---|
| Model | **Vertex AI**: Claude (default, `PSAI_LLM=vertex`) or Gemini (`PSAI_LLM=gemini`, set `PSAI_MODEL`) |
| OCR | **Document AI** Enterprise Document OCR (`--ocr documentai`), far better on Arabic scans than Tesseract |
| App | **Cloud Run** (`Dockerfile`), service account with `roles/aiplatform.user` |
| Private library | one **Cloud Storage** bucket mounted read-only at `/data` (`library.sqlite`); the PDFs and page text never go in the image or the repo |

```bash
# better OCR for the weak pages, then rebuild (create a Document AI "Enterprise Document OCR" processor first)
export PSAI_DOCAI_PROCESSOR=projects/PROJECT/locations/eu/processors/ID
python -m psai.ingest --pdf-dir /path/to/pdfs --ocr documentai --reocr-below 0.6

PROJECT=my-project REGION=europe-west1 BUCKET=my-psai-library ACCESS_CODE=choose ./scripts/deploy_gcp.sh
```

The deploy script creates the bucket and service account, uploads the index, and deploys Cloud Run with
**IAM-only access** (`--no-allow-unauthenticated`). Opening it to people who only have the access code is a deliberate
second step (see the script's last lines); keep the rights position in mind first. Claude on Vertex needs a one-time
enable in Model Garden. The Vertex/Gemini/Document AI paths are written to the documented APIs but have not been run
against a live project yet.

## Not built yet (designed for)

* **Voice / avatar:** `ask()` returns a plain, already-verified result (`answer_ar`, `quotes`); a later
  voice or video step should consume only that, never free text.
* **Embeddings:** add a vector table beside `chunks` and fuse it in `search()`; today retrieval is keyword-only.
* **Gates before any public use:** rights holder licence, Church liaison, clergy-reviewed test set (200–300 questions),
  and a "report this answer" flow.
* OCR is raw Tesseract; pages with a low score are flagged in `books.n_low_quality` for a Claude-vision re-OCR
  (`--ocr claude --force`) or human proofreading. Scripture references are the weakest part of OCR output.
