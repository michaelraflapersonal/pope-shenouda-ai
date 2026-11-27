import json
from types import SimpleNamespace

from psai.arabic import clean_display, normalize_search, text_quality, stem
from psai.answer import ask, verify_quotes
from psai.ingest import chunk_pages, cite_pages
from psai.search import Passage, search
from psai import store

AR = "ويقول الرب يسوع المسيح إن الله محبة ، ومن يثبت في المحبة يثبت في الله والله فيه . " * 8


def test_presentation_forms_are_folded():
    assert clean_display("ﻜﻴﻑ ﺘﺨﺘﻠﻑ ﻗﻴﺎﻤﺔ ﺍﻟﻤﺴﻴﺢ") == "كيف تختلف قيامة المسيح"
    assert clean_display("قدوس االله") == "قدوس الله"


def test_search_normalisation_unifies_variants():
    assert normalize_search("والمحبة") == normalize_search("محبته") == "محب"
    assert stem("الله") == "الله"


def test_quality_prefers_real_arabic():
    assert text_quality(AR) > 0.6
    assert text_quality(AR[::-1]) < 0.3


def _pages(n):
    return [{"pdf_page": i + 1, "printed_page": i + 1 if i % 3 == 0 else None, "method": "text",
             "quality": 1.0, "text": AR} for i in range(n)]


def test_chunks_carry_pages():
    ch = chunk_pages(_pages(6))
    assert ch and all(c["page_from"] <= c["page_to"] for c in ch)
    assert ch[0]["page_from"] == 1


def test_bad_ocr_page_number_is_ignored():
    pages = _pages(9)
    pages[4]["printed_page"] = 75
    assert cite_pages(pages)[4] == 5


def test_search_and_quote_verification(tmp_path):
    con = store.connect(tmp_path / "t.sqlite")
    con.execute("INSERT INTO books VALUES(1,'ar-x','ar','Love','x.pdf','h',1,0,0,NULL)")
    text = "المحبة هي أعظم الفضائل ، لأن الله محبة ، ومن لا يحب لم يعرف الله ."
    con.execute("INSERT INTO chunks VALUES(1,1,0,7,7,7,7,1.0,?)", (text,))
    from psai.arabic import normalize_search as ns
    con.execute("INSERT INTO chunks_fts(rowid,norm) VALUES(1,?)", (ns(text),))
    con.commit()
    ps = search("ما هي المحبة", con=con)
    assert ps and ps[0].book == "Love" and ps[0].page_from == 7
    ok, dropped = verify_quotes(
        [{"passage": 1, "text": "الله محبة ، ومن لا يحب لم يعرف الله"},
         {"passage": 1, "text": "المحبة تغطي كثرة من الخطايا وتسامح كل أحد"}], ps)
    assert len(ok) == 1 and dropped == 1 and ok[0]["cite"].endswith("7")


def fake_llm(monkeypatch, answer):
    """Stand in for the model: first call = search plan, second = answer."""
    calls = [{"language": "ar", "search_queries_ar": ["المحبة"], "crisis": False}, answer]
    monkeypatch.setattr("psai.llm.generate_json", lambda *a, **k: calls.pop(0))


def _lib(tmp_path):
    con = store.connect(tmp_path / "t.sqlite")
    con.execute("INSERT INTO books VALUES(1,'ar-x','ar','Love','x.pdf','h',1,0,0,NULL)")
    text = "المحبة هي أعظم الفضائل ، لأن الله محبة ، ومن لا يحب لم يعرف الله ."
    con.execute("INSERT INTO chunks VALUES(1,1,0,7,7,7,7,1.0,?)", (text,))
    from psai.arabic import normalize_search as ns
    con.execute("INSERT INTO chunks_fts(rowid,norm) VALUES(1,?)", (ns(text),))
    con.commit()
    return con


def test_answer_withheld_without_verifiable_quote(tmp_path, monkeypatch):
    con = _lib(tmp_path)
    fake_llm(monkeypatch, {"found": True, "crisis": False, "answer_ar": "المحبة مهمة [1]", "answer_en": "",
                       "quotes": [{"passage": 1, "text": "نص مخترع لا وجود له في الكتاب أبدا"}]})
    r = ask("ما هي المحبة؟", con=con)
    assert not r.found and r.quotes == []


def test_good_answer_is_returned_with_citation(tmp_path, monkeypatch):
    con = _lib(tmp_path)
    fake_llm(monkeypatch, {"found": True, "crisis": False, "answer_ar": "المحبة أعظم الفضائل [1]", "answer_en": "",
                       "quotes": [{"passage": 1, "text": "لأن الله محبة"}]})
    r = ask("ما هي المحبة؟", con=con)
    assert r.found and r.quotes[0]["cite"] == "Love، ص 7" and r.sources[0]["n"] == 1


def test_provider_autodetect(monkeypatch):
    from psai import llm
    monkeypatch.delenv("PSAI_LLM", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
    assert llm.provider() == "anthropic"
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "p")
    assert llm.provider() == "vertex"
    monkeypatch.setenv("PSAI_LLM", "gemini")
    assert llm.provider() == "gemini" and llm.model_name().startswith("gemini")


def test_readonly_library(tmp_path, monkeypatch):
    con = _lib(tmp_path)
    con.close()
    monkeypatch.setenv("PSAI_READONLY", "1")
    ro = store.connect(tmp_path / "t.sqlite")
    assert ro.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 1
