"""Ask from the terminal: ``python -m psai.cli "سؤالك"``."""
import json
import sys

from .answer import DISCLAIMER_AR, ask

if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) or input("السؤال: ")
    r = ask(q)
    print(r.answer_ar)
    if r.answer_en:
        print("\n" + r.answer_en)
    for qt in r.quotes:
        print(f"\n« {qt['text']} »\n   — {qt['cite']}")
    for n in r.notes:
        print(f"[note] {n}", file=sys.stderr)
    print("\n" + DISCLAIMER_AR)
