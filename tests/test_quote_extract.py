"""OCR-aware reader + general quote extractor."""
from __future__ import annotations

import types

from hermes.command_center import extract as E
from hermes.command_center import quote_extract as Q


# ---- fake LLM plumbing ----------------------------------------------------

def _fake_client(content: str, sink: dict | None = None):
    def create(**kwargs):
        if sink is not None:
            sink.update(kwargs)
        msg = types.SimpleNamespace(message=types.SimpleNamespace(content=content))
        return types.SimpleNamespace(choices=[msg])

    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))


def _patch_model(monkeypatch, content: str, sink: dict | None = None):
    monkeypatch.setattr(Q, "_model", lambda: (_fake_client(content, sink), "top-model"))


# ---- OCR-aware reader -----------------------------------------------------

def test_read_document_text_uses_text_layer(monkeypatch):
    monkeypatch.setattr(E, "read_text", lambda p: "x" * 200)
    called = {"ocr": False}
    import hermes.command_center.ocr as O
    monkeypatch.setattr(O, "ocr_pdf", lambda p: called.__setitem__("ocr", True) or "OCR")
    assert E.read_document_text("dec.pdf") == "x" * 200
    assert called["ocr"] is False  # substantive text layer -> no OCR spend


def test_read_document_text_falls_back_to_ocr(monkeypatch):
    monkeypatch.setattr(E, "read_text", lambda p: "   ")  # thin/scanned
    import hermes.command_center.ocr as O
    monkeypatch.setattr(O, "ocr_pdf", lambda p: "OCR TEXT FROM VISION")
    assert E.read_document_text("scan.pdf") == "OCR TEXT FROM VISION"


def test_read_document_text_skips_ocr_for_non_pdf(monkeypatch):
    monkeypatch.setattr(E, "read_text", lambda p: "")
    import hermes.command_center.ocr as O
    monkeypatch.setattr(O, "ocr_pdf", lambda p: (_ for _ in ()).throw(AssertionError("should not OCR")))
    assert E.read_document_text("notes.txt") == ""


# ---- JSON tolerance + cleaning -------------------------------------------

def test_loads_tolerates_code_fences():
    assert Q._loads('```json\n{"carrier": "Acme"}\n```') == {"carrier": "Acme"}


def test_clean_drops_unknown_and_empty():
    raw = {"carrier": "Acme", "premium": 1200, "bogus": "x", "deductible": "", "coverage_limits": {}}
    assert Q._clean(raw) == {"carrier": "Acme", "premium": 1200}


# ---- extract_quote --------------------------------------------------------

def test_extract_quote_text_path(monkeypatch):
    sink: dict = {}
    _patch_model(monkeypatch, '{"carrier":"Travelers","policy_number":"BA-99","premium":"3,400","junk":1}', sink)
    out = Q.extract_quote(text="a dec page with real content")
    assert out == {"carrier": "Travelers", "policy_number": "BA-99", "premium": "3,400"}
    assert sink.get("response_format") == {"type": "json_object"}  # json mode on text


def test_extract_quote_vision_path_omits_json_mode(monkeypatch):
    sink: dict = {}
    _patch_model(monkeypatch, '{"carrier":"Hartford"}', sink)
    out = Q.extract_quote(images=[b"\x89PNG-fake"])
    assert out == {"carrier": "Hartford"}
    assert "response_format" not in sink  # vision path skips json_object mode
    assert isinstance(sink["messages"][1]["content"], list)  # image blocks


def test_extract_quote_empty_input_returns_empty():
    assert Q.extract_quote(text="   ") == {}
    assert Q.extract_quote() == {}


# ---- full pipeline --------------------------------------------------------

def test_from_pdf_text_path(monkeypatch):
    monkeypatch.setattr("hermes.command_center.extract.read_text", lambda p: "y" * 100)
    monkeypatch.setattr(Q, "extract_quote", lambda **kw: {"carrier": "X"})
    res = Q.extract_quote_from_pdf("q.pdf")
    assert res["ocr_used"] is False and res["fields"] == {"carrier": "X"}


def test_from_pdf_ocr_path(monkeypatch):
    monkeypatch.setattr("hermes.command_center.extract.read_text", lambda p: "")
    monkeypatch.setattr("hermes.command_center.ocr.render_pdf_to_images", lambda p: [b"png1", b"png2"])
    monkeypatch.setattr(Q, "extract_quote", lambda **kw: {"carrier": "Scanned Co"})
    res = Q.extract_quote_from_pdf("scan.pdf")
    assert res["ocr_used"] is True and res["pages"] == 2 and res["fields"]["carrier"] == "Scanned Co"
