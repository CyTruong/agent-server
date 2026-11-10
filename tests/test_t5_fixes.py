"""The remaining fixes from the t2/t4/t5 run."""
import asyncio

import pytest

import grounding
import llm
import runner
from runner import _same_name
from test_runner import make_runner, ocr_box, prepared_runner, reply, scripted_llm, tool_call

SUMMARY = "Tóm tắt văn bản:\n\n1. **Tên dự án**: Bo mạch\n2. **Khách hàng**: bán lẻ"


def typing_runner(monkeypatch, groups):
    tr = make_runner(monkeypatch)
    tr._task_groups = groups
    tr.confirms = []

    async def confirm(task_id, name, args, message=""):
        tr.confirms.append(args)
        return True
    tr._ask_confirm = confirm
    return tr


# 1. multi-line text ---------------------------------------------------------------

def test_chat_message_is_typed_line_by_line_without_markdown(monkeypatch):
    async def go():
        tr = typing_runner(monkeypatch, ("zalo",))
        result = await tr._run_tool("t", "type", {"text": SUMMARY})
        assert result.startswith("ok") and "3 lần xuống dòng" in result
        assert tr.relay.calls == [
            ("type", {"text": "Tóm tắt văn bản:"}),
            ("hotkey", {"combo": "shift+enter"}),
            ("hotkey", {"combo": "shift+enter"}),
            ("type", {"text": "1. Tên dự án: Bo mạch"}),
            ("hotkey", {"combo": "shift+enter"}),
            ("type", {"text": "2. Khách hàng: bán lẻ"}),
        ]
        assert tr.confirms == []                      # no confirmation for line breaks
        assert all("\n" not in args.get("text", "") for _, args in tr.relay.calls)
    asyncio.run(go())


def test_word_uses_enter_and_keeps_markdown_text(monkeypatch):
    async def go():
        tr = typing_runner(monkeypatch, ("word",))
        await tr._run_tool("t", "type", {"text": "Dòng 1\n**Dòng 2**"})
        assert tr.relay.calls == [("type", {"text": "Dòng 1"}), ("key", {"name": "enter"}),
                                  ("type", {"text": "**Dòng 2**"})]
    asyncio.run(go())


def test_line_breaks_outside_apps_still_need_confirmation(monkeypatch):
    async def go():
        tr = typing_runner(monkeypatch, ())
        await tr._run_tool("t", "type", {"text": "a\nb"})
        assert tr.confirms
    asyncio.run(go())


def test_command_text_in_chat_still_needs_confirmation(monkeypatch):
    async def go():
        tr = typing_runner(monkeypatch, ("zalo",))
        await tr._run_tool("t", "type", {"text": "powershell -c x\nok"})
        assert tr.confirms
    asyncio.run(go())


# 2. identical failures ------------------------------------------------------------

def test_third_identical_failure_is_not_sent(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        tr.relay.device_error = asyncio.TimeoutError()
        same = {"text": "xin chào"}
        seen = scripted_llm(monkeypatch, [reply(calls=[tool_call("type", **same)])] * 3
                            + [reply("không gõ được")])
        await tr.run_task("t", "gõ xin chào")
        assert [c[0] for c in tr.relay.calls] == ["type", "type"]
        tools = [m["content"] for m in seen[-1]["messages"]
                 if isinstance(m, dict) and m.get("role") == "tool"]
        assert "KHÔNG lặp lại" in tools[1] and tools[2].startswith("error: KHÔNG chạy")
    asyncio.run(go())


# 3. coordinates in the model's units -------------------------------------------------

def test_tap_reports_image_coordinates(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [ocr_box("Gửi", 60, 30)])
        result = await tr._tap("t", {"target": "nút Gửi", "keywords": ["Gửi"]})
        assert "tại điểm (60,30) trên ảnh" in result
        assert tr.relay.calls[0][1] == {"x": 120, "y": 60, "button": 1}   # screen
    asyncio.run(go())


def test_move_reports_image_coordinates(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [])
        monkeypatch.setattr(grounding, "REFINE_CLICK", False)
        result = await tr._run_tool("t", "move", {"x": 40, "y": 20})
        assert result == "ok: move tại điểm (40,20) trên ảnh"
        assert tr.relay.calls == [("move", {"x": 80, "y": 40})]
    asyncio.run(go())


# 4. reading mode only when reading ------------------------------------------------------

def reading_mode_run(monkeypatch, request, calls):
    async def go():
        tr = make_runner(monkeypatch)
        monkeypatch.setattr(runner, "READ_NOTES", "model")
        copied = []

        async def fake_shot(task_id, *, force=False, attach=True):
            tr._raw_frame = b"frame"
            return "ok"
        tr._screenshot = fake_shot

        async def fake_transcribe(task_id):
            copied.append(1)
            return "nội dung"
        tr._transcribe_page = fake_transcribe
        # The "does it send?" check only runs for requests with a delivery word.
        check = [reply("CO")] if runner._needs_delivery(request) else []
        scripted_llm(monkeypatch, check + [reply(calls=[c]) for c in calls] + [reply("xong")])
        await tr.run_task("t", request)
        return copied
    return asyncio.run(go())


def test_sending_does_not_transcribe_pages(monkeypatch):
    copied = reading_mode_run(monkeypatch, "Gửi tóm tắt trên cho anh Vinh qua zalo",
                              [tool_call("screenshot")])
    assert copied == []


def test_windows_search_panel_is_not_transcribed(monkeypatch):
    copied = reading_mode_run(monkeypatch, "đọc file word hồ sơ",
                              [tool_call("hotkey", combo="win+s"), tool_call("type", text="Ho-so"),
                               tool_call("screenshot"), tool_call("screenshot")])
    assert copied == [1]                     # only the screenshot after the search closed


# 5. transcript marker -----------------------------------------------------------------

@pytest.mark.parametrize("answer,expected", [
    ("KHÔNG CÓ NỘI DUNG MỚI", ""),
    ("Không có nội dung mới.", ""),
    ("1. Tên dự án: Bo mạch máy bán hàng\nKHÔNG CÓ NỘI DUNG MỚI",
     "1. Tên dự án: Bo mạch máy bán hàng"),
])
def test_marker_only_counts_when_it_is_the_whole_answer(monkeypatch, answer, expected):
    async def go():
        tr = make_runner(monkeypatch)
        tr._raw_frame = b"page"

        async def fake_chat(**kwargs):
            return reply(answer)
        monkeypatch.setattr(llm, "chat", fake_chat)
        assert await tr._transcribe_page("t") == expected
    asyncio.run(go())


# 6. name labels and Enter in Windows Search -----------------------------------------------

def test_confirmed_name_replaces_the_ocr_text(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [])
        box = ocr_box("2 Vinh", 50, 20)
        tr._name_crop = lambda b: b"png"

        async def read(task_id, png):
            return ["A Vinh"]
        tr._read_crop = read
        res = grounding.NameResolution("uncertain", box, [box])
        out = await tr._reread_names("t", ["A Vinh"], res)
        assert out.status == "certain" and out.box["text"] == "A Vinh"
        assert out.candidates[0]["text"] == "A Vinh"
    asyncio.run(go())


@pytest.mark.parametrize("query,shown,expected", [
    ("Zalo", "Zalo", True),
    ("Ho-so-du-an-bo-mach-may-ban-hang-tu-dong", "Ho-so-du-an-bo-mach-may-ban-hang-tu-dong.docx", True),
    ("Ho-so-du-an-bo-mach-may-ban-hang-tu-dong", "Ho-so-du-an-bo-mach-may-ban-hang-...", True),
    ("Ho-so", "Ho-so-cu.docx", False),
    ("Zalo", "Zalo Web", False),
    ("Zalo", "Zal...", False),
])
def test_same_name(query, shown, expected):
    assert _same_name(query, shown) is expected


def search_runner(monkeypatch, top_text):
    tr = make_runner(monkeypatch)
    tr._search_open, tr._search_query = True, "Zalo"

    async def fake_shot(task_id, *, force=False, attach=True):
        tr._pending_image = b"shot"
        return "ok"
    tr._screenshot = fake_shot

    async def items(task_id):
        return [ocr_box("Best match", 100, 100), ocr_box(top_text, 110, 140),
                ocr_box("App", 110, 160)]
    tr._ocr_items = items
    return tr


def test_matching_top_result_is_opened_with_enter(monkeypatch):
    async def go():
        tr = search_runner(monkeypatch, "Zalo")
        result = await tr._run_tool("t", "key", {"name": "enter"})
        assert result.startswith("ok: kết quả đầu tiên 'Zalo'")
        assert tr.relay.calls == [("key", {"name": "enter"})]
    asyncio.run(go())


def test_other_top_result_waits_for_the_model(monkeypatch):
    async def go():
        tr = search_runner(monkeypatch, "Zalo Web")
        result = await tr._run_tool("t", "key", {"name": "enter"})
        assert "CHƯA nhấn Enter" in result and tr.relay.calls == []
    asyncio.run(go())
