"""Taps without OCR: UI-TARS points, the vision model reads names back."""
import asyncio

import grounding
from test_runner import make_runner

REQUEST = "Gửi lời chào đến anh Vinh qua zalo cho tôi"
LIST_TAP = {"target": "cuộc trò chuyện với A Vinh trong danh sách bên trái",
            "keywords": ["A Vinh"], "area": "left"}
INPUT_TAP = {"target": "ô nhập tin nhắn ở đáy khung trò chuyện",
             "keywords": ["Nhập @", "tin nhắn tới"], "anchor": ["A Vinh"],
             "relation": "bottom_of", "area": "bottom"}


def vision_runner(monkeypatch, point=(300, 600), reads=None, confirm=False, request=REQUEST):
    """A runner on a 1280x720 shot of a 1920x1080 screen. UI-TARS answers `point`
    (screen pixels); the vision model reads reads[purpose] for each region."""
    tr = make_runner(monkeypatch)
    monkeypatch.setattr(grounding, "SAVE_IMAGES", False)
    tr._task_text = request
    tr._raw_frame, tr._frame_hash = b"frame", "h"
    tr._frame_at = grounding.time.monotonic()
    tr._img_w, tr._img_h, tr._scr_w, tr._scr_h = 1280, 720, 1920, 1080
    tr.tars_calls, tr.read_calls, tr.asked = [], [], []

    async def tars(task_id, target, area="any", anchor_kws=None, relation="", keywords=None):
        tr.tars_calls.append({"target": target, "area": area, "anchor": anchor_kws,
                              "relation": relation, "keywords": keywords})
        return point
    tr._tars_ground = tars

    async def read(task_id, x, y, w, h, purpose=""):
        tr.read_calls.append((purpose, (x, y, w, h)))
        return (reads or {}).get(purpose)
    tr._read_region = read

    async def ask(task_id, name, args, message=""):
        tr.asked.append(message)
        return confirm
    tr._ask_confirm = ask
    return tr


HEADER_READ = "đầu khung trò chuyện Zalo"
POINT_READ = "một dòng/nhãn trên giao diện"


def tap(tr, args):
    return asyncio.run(tr._tap("t", dict(args)))


def test_every_tap_is_located_by_ui_tars(monkeypatch):
    tr = vision_runner(monkeypatch, point=(960, 300), request="mở notepad")
    result = tap(tr, {"target": "nút Lưu", "keywords": ["Lưu"], "area": "top"})
    assert "(ui-tars)" in result and "tại điểm (640,200) trên ảnh" in result
    assert tr.relay.calls == [("click", {"x": 960, "y": 300, "button": 1})]
    assert tr.tars_calls[0]["keywords"] == ["Lưu"]


def test_no_answer_from_ui_tars_is_an_error_not_a_guess(monkeypatch):
    tr = vision_runner(monkeypatch, point=None, request="mở notepad")
    assert tap(tr, {"target": "nút Lưu"}).startswith("error: UI-TARS không tìm thấy")
    assert tr.relay.calls == []


def test_top_area_point_elsewhere_is_refused(monkeypatch):
    tr = vision_runner(monkeypatch, point=(960, 800), request="mở word")
    assert tap(tr, {"target": "nút Vertical trên ribbon", "area": "top"}).startswith("error")
    assert tr.relay.calls == []


def test_open_chat_is_not_clicked_again(monkeypatch):
    tr = vision_runner(monkeypatch, reads={HEADER_READ: "A Vinh\nVừa truy cập"})
    assert "đã mở sẵn" in tap(tr, LIST_TAP)
    assert tr.relay.calls == [] and tr.tars_calls == []


def test_list_row_is_clicked_when_the_name_reads_back(monkeypatch):
    tr = vision_runner(monkeypatch, reads={HEADER_READ: "Bình", POINT_READ: "A Vinh\nBạn: chào anh"})
    assert tap(tr, LIST_TAP).startswith("ok")
    assert tr.relay.calls == [("click", {"x": 300, "y": 600, "button": 1})] and not tr.asked
    purpose, (x, y, w, h) = tr.read_calls[-1]
    assert purpose == POINT_READ and x <= 300 <= x + w and y <= 600 <= y + h


def test_lookalike_name_at_the_point_is_asked_and_refused(monkeypatch):
    tr = vision_runner(monkeypatch, reads={HEADER_READ: "Bình", POINT_READ: "Vinh Kế toán"})
    result = tap(tr, LIST_TAP)
    assert "Vinh Kế toán" in tr.asked[0] and result.startswith("error")
    assert tr.relay.calls == []


def test_lookalike_accepted_by_the_user_is_clicked(monkeypatch):
    tr = vision_runner(monkeypatch, reads={POINT_READ: "Vĩnh"}, confirm=True,
                       request="nhắn cho Vinh trên facebook")
    assert tap(tr, {"target": "liên hệ Vinh", "keywords": ["Vinh"]}).startswith("ok")
    assert len(tr.relay.calls) == 1


def test_input_needs_the_right_conversation_open(monkeypatch):
    tr = vision_runner(monkeypatch, reads={HEADER_READ: "Bình"})
    result = tap(tr, INPUT_TAP)
    assert "Bình" in tr.asked[0] and result.startswith("error") and tr.relay.calls == []


def test_input_under_the_right_header_is_clicked(monkeypatch):
    tr = vision_runner(monkeypatch, point=(538, 1008), reads={HEADER_READ: "AVinh"})
    assert tap(tr, INPUT_TAP).startswith("ok") and not tr.asked
    assert tr.tars_calls[0]["anchor"] == ["A Vinh"]
    assert tr.tars_calls[0]["relation"] == "bottom_of"


def test_different_person_from_the_request_is_asked(monkeypatch):
    tr = vision_runner(monkeypatch, reads={POINT_READ: "Bình"})
    result = tap(tr, {**LIST_TAP, "keywords": ["Bình"]})
    assert "Bình" in tr.asked[0] and result.startswith("error") and tr.relay.calls == []


def test_ui_label_is_not_checked_as_a_name(monkeypatch):
    tr = vision_runner(monkeypatch, request="Gửi tin cho An")
    assert tap(tr, {"target": "nút Gửi", "keywords": ["Gửi"]}).startswith("ok")
    assert tr.read_calls == [] and not tr.asked


def test_check_name_reads_the_image_without_hinting_the_name(monkeypatch):
    import llm

    async def go():
        tr = make_runner(monkeypatch)
        prompts = []

        async def fake_chat(**kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return {"message": {"content": "A Vinh"}}
        monkeypatch.setattr(llm, "chat", fake_chat)
        assert await tr._read_image("t", b"img", "một dòng") == "A Vinh"
        assert "Vinh" not in prompts[0]
    asyncio.run(go())
