import unicodedata

import pytest

from matching import is_proper_name, name_match


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


