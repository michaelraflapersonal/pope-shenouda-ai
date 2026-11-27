"""Grounded answering: retrieve passages, have Claude answer only from them,
then verify every quotation is word-for-word in the cited passage.

Guardrails (see SYSTEM_PROMPT and ``ask``):
* the model sees only the retrieved passages and may use nothing else;
* nothing relevant found -> a fixed "he did not address this in the books we have" reply;
* quotes are checked programmatically against the source text; unverifiable quotes are dropped;
* never speaks in the first person as him; always labelled as AI, not the Pope;
* grief/crisis/confession questions are pointed to a priest (and emergency help).

The result is a plain dict so a voice or avatar layer can later consume only
``answer_ar`` / ``quotes`` of an already-checked answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import llm
from .arabic import normalize_letters, strip_diacritics
from .search import Passage, search

DISCLAIMER_AR = "هذه إجابة من برنامج ذكاء اصطناعي مبنية على كتب البابا شنوده الثالث، وليست كلامه هو ولا تمثل الكنيسة. راجع أب اعترافك في أمورك الروحية."
DISCLAIMER_EN = "This answer comes from an AI built on Pope Shenouda III's books. It is not Pope Shenouda and does not speak for the Church. For personal spiritual guidance, please speak to your priest."
NOT_FOUND_AR = "لم أجد في الكتب المتاحة لدي ما يتناول هذا السؤال. يُفضّل أن تسأل أب اعترافك أو أحد الآباء الكهنة."
NOT_FOUND_EN = "I could not find this addressed in the books available to me. Please ask your priest."
CRISIS_AR = "إن كنت تمر بضيقة شديدة أو تفكر في إيذاء نفسك، فأرجو أن تتحدث الآن مع أب اعترافك أو شخص تثق به، أو تتصل بخدمات الطوارئ في بلدك."
CRISIS_EN = "If you are in deep distress or thinking of harming yourself, please speak now to your priest or someone you trust, or contact emergency services in your country."

SYSTEM_PROMPT = """You are a research assistant that answers questions using ONLY passages from books by Pope Shenouda III, supplied in the user message as numbered passages. You are not Pope Shenouda and must never write as if you were him.

Rules:
1. Use only the numbered passages. Never add doctrine, facts, verses or examples from your own knowledge. If the passages do not answer the question, set "found" to false and leave the answer empty.
2. Quote him word for word. Every quote must be copied exactly (same spelling, same words, no changes) from one passage, with that passage's number. Keep each quote under about 60 words. Put his words in "quotes", not in "answer_ar".
3. "answer_ar" is a short explanation in clear Modern Standard Arabic that follows his teaching and style: short direct sentences, numbered points when he enumerates, simple warmth. It paraphrases only what the passages support. Refer to him in the third person ("يقول قداسة البابا شنوده") and never in the first person. Mention passage numbers like [2] after each claim so it can be traced.
4. If the question asks for personal spiritual direction, confession, a verdict on a specific person, Church politics, or anything after 2012, say in "answer_ar" that this is for the person's priest, and set "found" to false unless the passages speak directly to it.
5. If the user's message suggests grief, crisis or self-harm, set "crisis" to true and answer gently with what the passages offer, pointing them to their priest.
6. Passages are scanned and may contain OCR errors. Quote them as written; do not silently correct.
7. If the user wrote in English, also give "answer_en": a faithful, plain English rendering of answer_ar, labelled by the app as a machine translation. Otherwise leave it empty."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": ["ar", "en", "other"]},
        "search_queries_ar": {"type": "array", "items": {"type": "string"}},
        "crisis": {"type": "boolean"},
    },
    "required": ["language", "search_queries_ar", "crisis"],
    "additionalProperties": False,
}

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "crisis": {"type": "boolean"},
        "answer_ar": {"type": "string"},
        "answer_en": {"type": "string"},
        "quotes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"passage": {"type": "integer"}, "text": {"type": "string"}},
                "required": ["passage", "text"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["found", "crisis", "answer_ar", "answer_en", "quotes"],
    "additionalProperties": False,
}


def plan(question: str) -> dict:
    return llm.generate_json(
        "You prepare searches over Arabic books. Detect the question's language, write 2 to 4 Arabic search queries "
        "(keywords or short phrases, in the vocabulary Pope Shenouda III used, e.g. 'التوبة', 'الاتضاع') that would "
        "find passages answering it, and flag crisis=true if the message suggests grief, crisis or self-harm.",
        question, PLAN_SCHEMA, "low", 1000,
    )


def _squash(s: str) -> str:
    """Comparison form for quote checks: ignores diacritics, spacing and punctuation."""
    s = strip_diacritics(normalize_letters(s))
    return re.sub(r"[^\w]+", "", s)


def verify_quotes(quotes: list[dict], passages: list[Passage]) -> tuple[list[dict], int]:
    ok, dropped = [], 0
    for q in quotes:
        i = q["passage"] - 1
        if 0 <= i < len(passages) and len(_squash(q["text"])) >= 8 and _squash(q["text"]) in _squash(passages[i].text):
            p = passages[i]
            ok.append({"text": q["text"].strip(), "book": p.book, "page_from": p.page_from,
                       "page_to": p.page_to, "cite": p.cite, "passage": q["passage"]})
        else:
            dropped += 1
    return ok, dropped


def build_context(passages: list[Passage]) -> str:
    return "\n\n".join(f"[{i}] ({p.cite})\n{p.text}" for i, p in enumerate(passages, 1))


@dataclass
class Result:
    found: bool
    language: str
    answer_ar: str
    answer_en: str
    quotes: list[dict]
    sources: list[dict]
    crisis: bool
    notes: list[str]
    disclaimer_ar: str = DISCLAIMER_AR
    disclaimer_en: str = DISCLAIMER_EN

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def ask(question: str, k: int = 8, con=None) -> Result:
    pl = plan(question) or {"language": "ar", "search_queries_ar": [question], "crisis": False}
    lang = pl["language"]
    passages = search([question] + pl["search_queries_ar"], k=k, con=con)
    notes: list[str] = []
    if not passages:
        return Result(False, lang, NOT_FOUND_AR, NOT_FOUND_EN if lang == "en" else "", [], [], pl["crisis"], ["no passages"])

    user = f"Question ({lang}):\n{question}\n\nPassages:\n\n{build_context(passages)}"
    out = llm.generate_json(SYSTEM_PROMPT, user, ANSWER_SCHEMA, "medium", 6000)
    quotes, dropped = verify_quotes(out.get("quotes", []), passages)
    if dropped:
        notes.append(f"{dropped} quote(s) removed: not found word for word in the cited passage")
    found = bool(out.get("found")) and bool(out.get("answer_ar", "").strip())
    if found and not quotes:
        found = False
        notes.append("no verifiable quotation; answer withheld")
    crisis = bool(out.get("crisis") or pl["crisis"])
    used = sorted({q["passage"] for q in quotes} | {int(n) for n in re.findall(r"\[(\d+)\]", out.get("answer_ar", ""))
                                                    if 1 <= int(n) <= len(passages)})
    sources = [{"n": n, "cite": passages[n - 1].cite, "book": passages[n - 1].book,
                "page_from": passages[n - 1].page_from, "page_to": passages[n - 1].page_to,
                "text": passages[n - 1].text} for n in used]
    if not found:
        return Result(False, lang, out.get("answer_ar") or NOT_FOUND_AR if crisis else NOT_FOUND_AR,
                      NOT_FOUND_EN if lang == "en" else "", [], [], crisis, notes)
    return Result(True, lang, out["answer_ar"], out.get("answer_en", "") if lang == "en" else "",
                  quotes, sources, crisis, notes)
