"""Name rules from the real run 'Gửi lời chào đến anh Vinh qua zalo'."""
import pytest

from matching import is_proper_name, name_matches_request, requested_persons

REQUEST = "Gửi lời chào đến anh Vinh qua zalo cho tôi"


@pytest.mark.parametrize("request_text,expected", [
    (REQUEST, [["vinh"]]),
    ("nhắn cho A Vinh", [["vinh"]]),
    ("nhắn cho An và Bình", [["an"], ["bình"]]),
    ("nhắn cho Zalo", []),
    ("nhan cho vinh", []),
])
def test_requested_persons(request_text, expected):
    assert requested_persons(request_text) == expected


@pytest.mark.parametrize("keyword,request_text,expected", [
    ("A Vinh", REQUEST, True),
    ("Trần Vinh", REQUEST, True),
    ("Bình", REQUEST, False),
    ("Tường", "Nhắn cho Trường", False),
    ("Tám", "Nhắn cho chị Tâm", False),
    ("Trường", "nhan cho Truong", True),
    ("Bình", "trả lời người vừa nhắn", True),
])
def test_name_matches_request(keyword, request_text, expected):
    assert name_matches_request(keyword, request_text) is expected


def test_abbreviated_name_counts_as_from_the_request():
    assert is_proper_name("A Vinh", REQUEST, "ô nhập")
