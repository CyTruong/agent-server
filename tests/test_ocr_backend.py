"""OCR backend choice, with fake engine modules (no real OCR installed)."""
import io
import sys
import types

import pytest
from PIL import Image

import ocr


class FakeReader:
    def __init__(self, langs, gpu):
        self.langs, self.gpu = langs, gpu

    def readtext(self, image):
        return [([[10, 20], [60, 20], [60, 40], [10, 40]], "Trường", 0.9)]


class FakePaddle:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def engines(monkeypatch):
    """Install fake easyocr/paddleocr modules and reset the cached engine."""
    def install(easy=True, paddle=True):
        for name, present, attr, value in (("easyocr", easy, "Reader", FakeReader),
                                           ("paddleocr", paddle, "PaddleOCR", FakePaddle)):
            if present:
                module = types.ModuleType(name)
                setattr(module, attr, value)
                monkeypatch.setitem(sys.modules, name, module)
            else:
                monkeypatch.setitem(sys.modules, name, None)   # import raises
        monkeypatch.setattr(ocr, "_tried", False)
        monkeypatch.setattr(ocr, "_engine", None)
        monkeypatch.setattr(ocr, "_kind", None)
    return install


@pytest.mark.parametrize("setting,easy,paddle,expected", [
    ("auto", True, True, "easyocr"),
    ("auto", False, True, "paddle"),
    ("easyocr", False, True, None),
    ("paddle", True, True, "paddle"),
    ("unknown", True, True, "easyocr"),
])
def test_backend_choice(monkeypatch, engines, setting, easy, paddle, expected):
    engines(easy, paddle)
    monkeypatch.setenv("OCR_BACKEND", setting)
    assert ocr._load("vi")[0] == expected


def test_failure_reason_is_kept_and_load_is_retried_later(monkeypatch, engines):
    engines(easy=False, paddle=False)
    monkeypatch.setenv("OCR_BACKEND", "auto")
    assert ocr._load("vi") == (None, None)
    assert "easyocr" in ocr.load_error() and "paddle" in ocr.load_error()
    engines(easy=True, paddle=False)
    monkeypatch.setattr(ocr, "_tried", True)
    monkeypatch.setattr(ocr, "_tried_at", ocr.time.monotonic())
    assert ocr._load("vi") == (None, None)          # too soon to retry
    monkeypatch.setattr(ocr, "_tried_at", ocr.time.monotonic() - ocr.RETRY_SECONDS - 1)
    assert ocr._load("vi")[0] == "easyocr" and ocr.load_error() == ""


def test_easyocr_runs_on_cpu_with_vietnamese_and_english(monkeypatch, engines):
    engines()
    monkeypatch.setenv("OCR_BACKEND", "auto")
    _, engine = ocr._load("vi")
    assert engine.langs == ["vi", "en"] and engine.gpu is False


def test_easyocr_result_keeps_accents_and_box(monkeypatch, engines):
    engines()
    monkeypatch.setenv("OCR_BACKEND", "easyocr")
    out = io.BytesIO()
    Image.new("RGB", (100, 60)).save(out, format="PNG")
    [item] = ocr.run_ocr(out.getvalue(), "vi")
    assert item["text"] == "Trường" and (item["cx"], item["cy"]) == (35, 30)
