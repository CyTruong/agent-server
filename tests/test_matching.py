import unicodedata

import pytest

from matching import _sim, is_proper_name, name_match, resolve_name


def box(text):
    return {"text": text, "cx": 10, "cy": 10}


@pytest.mark.parametrize("name,text,expected", [
    ("Trường", "Trường", "exact"),
    ("trường", "TRƯỜNG", "exact"),
    ("Trường", "Phạm Nhật Trường", "suffix"),
    ("Trường", "Trường Đại học ABC", "partial"),
    ("Hải", "Hai", "accent"),
    ("Tâm", "Tám", "accent"),
    ("Trường", "Tường", "fuzzy"),
    ("Trường", "Bình", "none"),
])
def test_name_match(name, text, expected):
    assert name_match(name, text) == expected


def test_name_match_accepts_decomposed_ocr_text():
    assert name_match("Trường", unicodedata.normalize("NFD", "Trường")) == "exact"


def test_resolve_exact_is_certain_even_with_lookalikes():
    res = resolve_name(["Trường"], [box("Tường"), box("Trường")])
    assert res.status == "certain" and res.box["text"] == "Trường"


def test_resolve_single_full_name_is_certain():
    assert resolve_name(["Trường"], [box("Phạm Nhật Trường")]).status == "certain"


def test_resolve_two_different_full_names_is_uncertain():
    res = resolve_name(["Trường"], [box("Phạm Nhật Trường"), box("Lê Văn Trường")])
    assert res.status == "uncertain" and len(res.candidates) == 2


@pytest.mark.parametrize("seen", ["Tường", "Trường Đại học ABC", "Truong"])
def test_resolve_lookalike_only_is_uncertain(seen):
    res = resolve_name(["Trường"], [box(seen)])
    assert res.status == "uncertain" and res.box["text"] == seen


def test_resolve_absent_name():
    assert resolve_name(["Trường"], [box("Bình")]).status == "none"
    assert resolve_name(["Trường"], None).status == "none"


@pytest.mark.parametrize("keyword,request_text,target,expected", [
    ("Trường", "nhắn cho Trường là tôi đến muộn", "liên hệ Trường", True),
    ("Trường", "nhắn cho trường nhé", "khung chat", True),
    ("Phạm Nhật Trường", "tìm bạn", "liên hệ Phạm Nhật Trường", True),
    ("Gửi", "Gửi tin cho An", "nút Gửi", False),
    ("Aa", "nhắn cho An", "ô nhập tin nhắn", False),
    ("Tìm kiếm", "Tìm kiếm An", "ô tìm kiếm", False),
    ("Facebook", "mở Facebook", "logo Facebook", False),
])
def test_is_proper_name(keyword, request_text, target, expected):
    assert is_proper_name(keyword, request_text, target) is expected


def test_sim_requires_whole_word():
    assert _sim("Aa", "Aaron Nguyen") < 0.8
    assert _sim("Gửi", "Nút Gửi tin") >= 0.8


def test_sim_tolerates_ocr_accent_loss_for_labels():
    assert _sim("Tìm kiếm", "Tim kiem") == 1.0
