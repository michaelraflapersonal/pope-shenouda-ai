"""Arabic text normalisation.

Two levels:

* ``clean_display`` repairs extracted text for showing and quoting: it folds
  presentation forms (U+FB50..U+FEFF) back to base letters, maps Persian/Urdu
  look-alike letters that old fonts emit to their Arabic equivalents, removes
  tatweel and stray control characters, and tidies whitespace. It keeps
  diacritics and the spelling as printed.
* ``normalize_search`` is a lossy form used only for matching: no diacritics,
  unified alef/yeh/teh marbuta, Western digits, light stemming.
"""

from __future__ import annotations

import re
import unicodedata

# Letters some old Arabic fonts map to Persian/Urdu code points.
_LOOKALIKES = str.maketrans({
    "ھ": "ه",  # ھ heh doachashmee -> ه
    "ہ": "ه",  # ہ heh goal -> ه
    "ی": "ي",  # ی farsi yeh -> ي
    "ے": "ي",  # ے yeh barree -> ي
    "ک": "ك",  # ک keheh -> ك
    "ـ": None,      # tatweel
    "‌": None, "‍": None, "‎": None, "‏": None,
    "‪": None, "‫": None, "‬": None, "‭": None, "‮": None,
    "﻿": None,
})

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_DIACRITICS = re.compile("[ؐ-ًؚ-ٰٟۖ-ۭ]")
_ARABIC_LETTER = re.compile("[ء-ي]")
_WORD = re.compile(r"[ء-ي٠-٩a-zA-Z0-9]+")


def clean_display(text: str) -> str:
    """Repair extracted Arabic for display, keeping it as printed."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_LOOKALIKES)
    # NFKC turns lam-alef ligatures into two letters already; collapse spaces.
    t = t.replace("\u0627\u0627\u0644\u0644\u0647", "\u0627\u0644\u0644\u0647")  # االله -> الله
    t = re.sub(r"[ \t ]+", " ", t)
    t = re.sub(r" *\n *", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def strip_diacritics(text: str) -> str:
    return _DIACRITICS.sub("", text)


def normalize_letters(text: str) -> str:
    """Fold spelling variants that books and OCR use interchangeably."""
    t = clean_display(text)
    t = strip_diacritics(t)
    t = re.sub("[إأآٱ]", "ا", t)
    t = t.replace("ى", "ي").replace("ة", "ه").replace("ؤ", "و").replace("ئ", "ي")
    t = t.translate(_DIGITS)
    return t


# Very light stemmer: strip common clitics and suffixes so that
# "والمحبة", "بالمحبة", "محبته" all meet "محبه".
_PREFIXES = ("وبال", "وال", "بال", "فال", "كال", "لل", "ال", "و", "ف", "ب", "ل", "ك")
_SUFFIXES = ("هما", "كما", "تها", "تهم", "ات", "ون", "ين", "ها", "هم", "كم", "نا", "ته", "يه", "ه", "ي")

STOPWORDS = frozenset(normalize_letters(w) for w in """
في من على الى إلى عن مع ان أن إن او أو ثم قد لا ما لم لن هل هو هي هم هن انت أنت نحن انا أنا
هذا هذه ذلك تلك الذي التي الذين كان كانت يكون تكون كل بعض كما لكن ولكن بل حتى اذا إذا
عند عندما بين فيه فيها به بها له لها منه منها عليه عليها اي أي ايضا أيضا جدا الا إلا وهو وهي
يا لقد هناك هنا ليس ليست غير اما أما فقد وقد قال يقول
the a an of to and in is are was were be it that this for on with as by at or not what how why who
""".split())


def stem(word: str) -> str:
    w = word
    if len(w) > 4:
        for p in _PREFIXES:
            if w.startswith(p) and len(w) - len(p) >= 3:
                w = w[len(p):]
                break
    if w == "الله":
        return w
    if len(w) > 4:
        for s in _SUFFIXES:
            if w.endswith(s) and len(w) - len(s) >= 3:
                return w[: -len(s)]
    if len(w) >= 4 and w.endswith("ه"):  # teh marbuta, folded to heh
        w = w[:-1]
    return w


def tokenize(text: str) -> list[str]:
    return _WORD.findall(normalize_letters(text).lower())


def normalize_search(text: str) -> str:
    """Space-separated stemmed tokens, for the full-text index and queries."""
    out = []
    for tok in tokenize(text):
        if tok in STOPWORDS:
            continue
        out.append(stem(tok))
    return " ".join(out)


def arabic_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if _ARABIC_LETTER.match(c)) / len(letters)


# Words that are very common in his prose; used to judge whether a text layer
# is real Arabic in logical order.
_COMMON = frozenset(normalize_letters(w) for w in
                    "في من على الله التي الذي هذا ان أن إلى الى عن كان لا ما كل الرب المسيح الانسان".split())


def text_quality(text: str) -> float:
    """Score 0..1 for how much a page's text looks like readable Arabic prose.

    Combines: share of Arabic letters, share of very common words (which drops
    when text is reversed or mis-encoded), and average word length (which drops
    when an old PDF puts spaces inside words).
    """
    words = tokenize(text)
    ar_words = [w for w in words if _ARABIC_LETTER.match(w[0])]
    if len(ar_words) < 15:
        return 0.0
    ratio = arabic_ratio(text)
    common = sum(1 for w in ar_words if w in _COMMON) / len(ar_words)
    avg_len = sum(len(w) for w in ar_words) / len(ar_words)
    len_ok = 1.0 if avg_len >= 3.4 else max(0.0, (avg_len - 2.4))
    common_ok = min(1.0, common / 0.06)
    return round(ratio * len_ok * common_ok, 3)


def reverse_visual_lines(text: str) -> str:
    """Undo visual (right-to-left stored left-to-right) order, line by line."""
    return "\n".join(line[::-1] for line in text.split("\n"))
