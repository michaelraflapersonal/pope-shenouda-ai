"""Page-level text extraction from the book PDFs.

For every page we try the PDF's own text layer first, repaired by
``arabic.clean_display`` (presentation forms -> letters, look-alike letters)
and, where it scores better, with each line reversed (some PDFs store Arabic
in visual order). If the repaired text still does not look like Arabic prose,
the page is rendered and OCR'd. Most of the Arabic collection is scanned
images with no text layer at all, so OCR is the common path.

OCR engines:
* ``tesseract`` (default): local, free, needs ``tesseract-ocr`` + ``tesseract-ocr-ara``.
* ``claude``: sends the page image to Claude for transcription. Slower and
  paid, but much more accurate on scripture references and similar letters.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import pymupdf

from .arabic import clean_display, reverse_visual_lines, text_quality

TEXT_LAYER_MIN_QUALITY = 0.55
LOW_QUALITY = 0.35

# Watermarks and site stamps found in the collection's text layers.
_NOISE_LINES = re.compile(
    r"^\s*(https?://\S+|www\.\S+|coptic-treasures\.com|St-Takla\.org.*)\s*$", re.I
)
_PAGE_NUM = re.compile(r"^[\s\-–—()\[\]]*([0-9٠-٩۰-۹]{1,4})[\s\-–—()\[\]]*$")
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


@dataclass
class Page:
    pdf_page: int            # 1-based index in the PDF
    printed_page: int | None  # page number printed on the page, if found
    method: str              # text | text-reversed | ocr-tesseract | ocr-claude | empty
    quality: float
    text: str


def _strip_noise(text: str) -> str:
    return "\n".join(l for l in text.split("\n") if not _NOISE_LINES.match(l))


def find_printed_page(text: str) -> tuple[int | None, str]:
    """Find a bare page number in the first or last two lines and remove it."""
    lines = text.split("\n")
    idx = [i for i, l in enumerate(lines) if l.strip()]
    candidates = idx[-2:][::-1] + idx[:2]
    for i in candidates:
        m = _PAGE_NUM.match(lines[i])
        if m:
            num = int(m.group(1).translate(_DIGITS))
            if 0 < num < 2000:
                del lines[i]
                return num, "\n".join(lines).strip()
    return None, text


_LTR_RUN = re.compile(r"[0-9A-Za-z\u0660-\u0669:.,/\-]+")
_MIRROR = str.maketrans("()[]«»", ")(][»«")


def text_layer_geometric(page: "pymupdf.Page") -> str:
    """Rebuild lines from glyph positions, right to left.

    Old Arabic PDFs scramble the stored order (reversed, split mid-word), but the
    glyph coordinates are right, so sorting glyphs by position recovers the text.
    """
    raw = page.get_text("rawdict")
    chars = []
    for b in raw["blocks"]:
        for l in b.get("lines", []):
            for sp in l["spans"]:
                for c in sp["chars"]:
                    if c["c"].strip():
                        x0, y0, x1, y1 = c["bbox"]
                        chars.append((y0, y1, x0, x1, c["c"], sp["size"] or 10))
    chars.sort(key=lambda c: (c[0] + c[1]) / 2)
    lines: list[dict] = []
    for c in chars:
        cy = (c[0] + c[1]) / 2
        if lines and abs(cy - lines[-1]["cy"]) < c[5] * 0.45:
            lines[-1]["chars"].append(c)
        else:
            lines.append({"cy": cy, "chars": [c]})
    out = []
    for ln in lines:
        cs = sorted(ln["chars"], key=lambda c: -c[3])
        buf, prev = [], None
        for c in cs:
            if prev is not None and prev[2] - c[3] > c[5] * 0.18:
                buf.append(" ")
            buf.append(c[4])
            prev = c
        line = "".join(buf)
        line = _LTR_RUN.sub(lambda m: m.group(0)[::-1], line)  # numbers read left to right
        out.append(line.translate(_MIRROR))
    return "\n".join(out)


def _best_text_layer(raw: str, page: "pymupdf.Page | None" = None) -> tuple[str, str, float]:
    forward = _strip_noise(clean_display(raw))
    q_f = text_quality(forward)
    if page is not None and q_f > 0.2:
        geo = _strip_noise(clean_display(text_layer_geometric(page)))
        q_g = text_quality(geo)
        if q_g >= TEXT_LAYER_MIN_QUALITY and q_g >= q_f - 0.02:
            return geo, "text-geometric", q_g
    if q_f >= TEXT_LAYER_MIN_QUALITY:
        return forward, "text", q_f
    backward = reverse_visual_lines(forward)
    q_b = text_quality(backward)
    if q_b > q_f:
        return backward, "text-reversed", q_b
    return forward, "text", q_f


def ocr_tesseract(png: bytes, lang: str = "ara") -> str:
    env = dict(os.environ, OMP_THREAD_LIMIT="1")
    proc = subprocess.run(
        ["tesseract", "stdin", "stdout", "-l", lang, "--psm", "4"],
        input=png, capture_output=True, env=env, check=True,
    )
    return proc.stdout.decode("utf-8", "replace")


_CLAUDE_OCR_PROMPT = (
    "Transcribe this scanned book page exactly as printed, in reading order. "
    "Keep the original Arabic spelling, punctuation, scripture references and numbering. "
    "Do not translate, summarise, correct or add anything. Skip decorative images. "
    "Put the printed page number, if any, on its own last line. Output only the text."
)


def ocr_claude(png: bytes, model: str | None = None) -> str:
    import anthropic

    client = anthropic.Anthropic()
    msg = client.messages.create(
        model=model or os.environ.get("PSAI_OCR_MODEL", "claude-opus-5-5"),
        max_tokens=8000,
        output_config={"effort": "low"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(png).decode()}},
                {"type": "text", "text": _CLAUDE_OCR_PROMPT},
            ],
        }],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def _ocr(png: bytes, engine: str, lang: str) -> str:
    if engine == "claude":
        return ocr_claude(png)
    return ocr_tesseract(png, "ara" if lang == "ar" else "eng")


def extract_pdf(path: str, lang: str = "ar", ocr_engine: str = "tesseract",
                force_ocr: bool = False, workers: int | None = None,
                dpi: int = 300) -> list[Page]:
    """Extract every page of a PDF. Pages that need OCR are OCR'd in parallel."""
    doc = pymupdf.open(path)
    pages: list[Page | None] = [None] * doc.page_count
    to_ocr: list[tuple[int, bytes]] = []
    min_q = TEXT_LAYER_MIN_QUALITY if lang == "ar" else 0.0

    for i, page in enumerate(doc):
        raw = page.get_text()
        if lang == "ar":
            text, method, q = _best_text_layer(raw, page)
        else:
            text = _strip_noise(clean_display(raw))
            method, q = "text", (1.0 if len(text.split()) > 20 else 0.0)
        if not force_ocr and q >= min_q and (lang == "ar" or q > 0):
            printed, body = find_printed_page(text)
            pages[i] = Page(i + 1, printed, method, q, body)
        else:
            pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            to_ocr.append((i, pix.tobytes("png")))
    doc.close()

    def run(item: tuple[int, bytes]) -> tuple[int, str]:
        i, png = item
        try:
            return i, _ocr(png, ocr_engine, lang)
        except Exception as exc:  # keep going; the page is marked empty
            return i, f"__OCR_ERROR__ {exc}"

    workers = workers or (4 if ocr_engine == "claude" else os.cpu_count() or 2)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, raw in pool.map(run, to_ocr):
            if raw.startswith("__OCR_ERROR__"):
                pages[i] = Page(i + 1, None, "empty", 0.0, "")
                continue
            text = _strip_noise(clean_display(raw))
            printed, body = find_printed_page(text)
            q = text_quality(body) if lang == "ar" else 1.0
            method = f"ocr-{ocr_engine}" if body.strip() else "empty"
            pages[i] = Page(i + 1, printed, method, q, body)

    return remove_running_headers([p for p in pages if p is not None])


def remove_running_headers(pages: list[Page]) -> list[Page]:
    """Drop short first/last lines that repeat on many pages (book title, chapter)."""
    from collections import Counter

    edge = Counter()
    for p in pages:
        lines = [l.strip() for l in p.text.split("\n") if l.strip()]
        for l in set(lines[:1] + lines[-1:]):
            if len(l) < 60:
                edge[l] += 1
    threshold = max(4, len(pages) // 5)
    repeated = {l for l, n in edge.items() if n >= threshold}
    if not repeated:
        return pages
    for p in pages:
        lines = p.text.split("\n")
        nonempty = [i for i, l in enumerate(lines) if l.strip()]
        drop = {i for i in nonempty[:1] + nonempty[-1:] if lines[i].strip() in repeated}
        p.text = "\n".join(l for i, l in enumerate(lines) if i not in drop).strip()
    return pages


def page_to_dict(p: Page) -> dict:
    return asdict(p)
