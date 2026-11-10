"""Regressions from the real run 'Gửi lời chào đến anh Vinh qua zalo' (1280x720)."""
import asyncio

import pytest

from matching import is_proper_name, name_matches_request, requested_persons
from test_runner import ocr_box, prepared_runner

REQUEST = "Gửi lời chào đến anh Vinh qua zalo cho tôi"
LIST_TAP = {"target": "cuộc trò chuyện với A Vinh trong danh sách bên trái",
            "keywords": ["A Vinh"], "area": "left"}
INPUT_TAP = {"target": "ô nhập tin nhắn ở đáy khung trò chuyện",
             "keywords": ["Nhập @", "tin nhắn tới"], "anchor": ["A Vinh"],
             "relation": "bottom_of", "area": "bottom"}
HEADER = ocr_box("A Vinh", 342, 32)
ROW = ocr_box("A Vinh", 109, 199)
FIELD = ocr_box("Nhap@.tin nhan toi A Vinh", 344, 672)


def t2_runner(monkeypatch, items, confirm=False, request=REQUEST):
    tr = prepared_runner(monkeypatch, items, confirm=confirm)
    tr._task_text = request
    tr._img_w, tr._img_h, tr._scr_w, tr._scr_h = 1280, 720, 1920, 1080

    async def none(*a, **k):
        return None
    tr._uia_marks = tr._tap_marks = tr._locate = none
    return tr


def tap(tr, args):
    return asyncio.run(tr._tap("t", dict(args)))


def test_open_chat_is_not_clicked_again(monkeypatch):
    tr = t2_runner(monkeypatch, [HEADER, ROW])
    assert "đã mở sẵn" in tap(tr, LIST_TAP)
    assert tr.relay.calls == [] and not tr.asked


def test_list_row_is_clicked_not_another_chats_header(monkeypatch):
    tr = t2_runner(monkeypatch, [ocr_box("Bình", 342, 32), ROW])
    assert tap(tr, LIST_TAP).startswith("ok")
    assert tr.relay.calls == [("click", {"x": 164, "y": 298, "button": 1})]


def test_name_outside_the_list_column_is_not_a_list_row(monkeypatch):
    tr = t2_runner(monkeypatch, [ocr_box("A Vinh", 342, 300)])
    tap(tr, LIST_TAP)
    assert tr.relay.calls == []


def test_reworded_anchor_is_checked_against_the_header(monkeypatch):
    tr = t2_runner(monkeypatch, [ocr_box("AVinh", 352, 32), FIELD])
    assert tap(tr, INPUT_TAP).startswith("ok")
    assert tr.relay.calls == [("click", {"x": 516, "y": 1008, "button": 1})] and not tr.asked


def test_stale_chat_is_refused_when_name_is_only_in_the_list(monkeypatch):
    tr = t2_runner(monkeypatch, [ocr_box("Bình", 342, 32), ROW, FIELD])
    tap(tr, INPUT_TAP)
    assert tr.asked and tr.relay.calls == []


def test_different_person_from_the_request_is_asked(monkeypatch):
    tr = t2_runner(monkeypatch, [ocr_box("Bình", 109, 199)])
    result = tap(tr, {**LIST_TAP, "keywords": ["Bình"]})
    assert "Bình" in tr.asked[0] and result.startswith("error") and tr.relay.calls == []


def test_answer_about_a_person_is_remembered_within_the_task(monkeypatch):
    tr = t2_runner(monkeypatch, [ocr_box("Bình", 109, 199)], confirm=True)
    tr._invalidate_screen_cache = lambda: None   # keep the frame for the second tap
    args = {**LIST_TAP, "keywords": ["Bình"]}
    tap(tr, args)
    tap(tr, args)
    assert len(tr.asked) == 1


def test_other_vinh_in_the_list_is_asked(monkeypatch):
    tr = t2_runner(monkeypatch, [ROW, ocr_box("Vinh Kế toán", 109, 260)])
    result = tap(tr, LIST_TAP)
    assert "Vinh Kế toán" in tr.asked[0] and result.startswith("error")


def test_other_vinh_stops_the_already_open_shortcut(monkeypatch):
    tr = t2_runner(monkeypatch, [HEADER, ROW, ocr_box("Vinh Kế toán", 109, 260)])
    tap(tr, LIST_TAP)
    assert tr.asked


def test_exact_contact_name_in_the_request_is_not_ambiguous(monkeypatch):
    tr = t2_runner(monkeypatch, [ocr_box("Bình", 342, 32), ROW, ocr_box("Vinh Kế toán", 109, 260)],
                   request="Gửi lời chào đến A Vinh qua zalo")
    assert tap(tr, LIST_TAP).startswith("ok") and not tr.asked


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
