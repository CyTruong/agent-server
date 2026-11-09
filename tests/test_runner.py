"""TaskRunner and RelayClient behaviour with a fake relay and a fake LLM."""
import asyncio
import base64
import io
import json
import ssl

import pytest
from PIL import Image

import grounding
import llm
import runner
from relay_client import RelayClient


class FakeRelay:
    frame_sw, frame_sh = 200, 100

    def __init__(self):
        self.ui, self.calls = [], []
        self.device_error = None

    async def send_ui(self, msg):
        self.ui.append(msg)

    async def call_device(self, tool, args, task_id, timeout=10.0, request_id=""):
        self.calls.append((tool, args))
        if self.device_error:
            raise self.device_error
        return {"ok": True, "result": {"detail": ""}}

    def final(self):
        return [m for m in self.ui if m["type"] in ("done", "error")]


def make_runner(monkeypatch, relay=None):
    monkeypatch.setattr(runner, "UI_LOGS", False)
    monkeypatch.setattr(runner, "CACHE_ENABLED", False)
    monkeypatch.setattr(runner, "PLAN_ENABLE", False)
    return runner.TaskRunner(relay or FakeRelay())


def reply(content="", calls=None):
    return {"message": {"content": content, "tool_calls": calls or []}}


def tool_call(name, **arguments):
    return {"function": {"name": name, "arguments": arguments}}


def scripted_llm(monkeypatch, replies):
    queue = list(replies)
    seen = []

    async def chat(**kwargs):
        seen.append(kwargs)
        return queue.pop(0)
    monkeypatch.setattr(llm, "chat", chat)
    return seen


def test_timeout_on_input_action_is_not_retried(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        tr.relay.device_error = asyncio.TimeoutError()
        result = await tr._run_tool("t", "type", {"text": "xin chào"})
        assert len(tr.relay.calls) == 1
        assert "KHÔNG rõ" in result
    asyncio.run(go())


def test_cancel_interrupts_a_hanging_llm(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)

        async def hang(**kwargs):
            await asyncio.sleep(60)
        monkeypatch.setattr(llm, "chat", hang)
        await tr.on_chat({"text": "xin chào", "task_id": "t1"})
        await asyncio.sleep(0.01)
        await tr.on_cancel({"task_id": "t1"})
        await asyncio.wait_for(tr._active, 1)
        assert tr.relay.final()[-1]["result"] == "đã huỷ"
    asyncio.run(go())


def test_task_timeout_reports_to_ui(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        monkeypatch.setattr(runner, "TASK_TIMEOUT", 0.01)

        async def hang(**kwargs):
            await asyncio.sleep(60)
        monkeypatch.setattr(llm, "chat", hang)
        await tr.run_task("t", "xin chào")
        assert tr.relay.final()[-1]["result"] == "hết thời gian"
    asyncio.run(go())


def test_crash_is_reported_not_silent(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)

        async def boom(**kwargs):
            return {"unexpected": True}
        monkeypatch.setattr(llm, "chat", boom)
        await tr.run_task("t", "xin chào")
        assert tr.relay.final()[-1]["type"] == "error"
    asyncio.run(go())


def test_bad_tool_arguments_do_not_kill_the_task(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        seen = scripted_llm(monkeypatch, [
            reply(calls=[tool_call("click", x="abc", y=1)]),
            reply("không bấm được"),
        ])
        await tr.run_task("t", "bấm giúp tôi")
        assert tr.relay.final()[-1]["result"] == "không bấm được"
        tool_results = [m for m in seen[-1]["messages"] if m.get("role") == "tool"]
        assert tool_results[0]["content"].startswith("error")
    asyncio.run(go())


def test_plain_question_with_delivery_word_is_not_pushed_to_send(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        scripted_llm(monkeypatch, [reply("KHONG"), reply("Bằng 4")])
        await tr.run_task("t", "trả lời giúp tôi 2+2 bằng mấy")
        assert tr.relay.final()[-1]["result"] == "Bằng 4"
    asyncio.run(go())


def test_real_delivery_request_is_still_nudged(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        seen = scripted_llm(monkeypatch, [reply("CO"), reply("Giá vàng là 100"), reply("Đã xong")])
        await tr.run_task("t", "nhắn cho An giá vàng")
        assert any("CHƯA XONG" in str(m.get("content")) for m in seen[-1]["messages"]
                   if isinstance(m, dict))
    asyncio.run(go())


# --- names in _tap -----------------------------------------------------------

def ocr_box(text, cx=50, cy=20):
    return {"text": text, "conf": 1.0, "x0": cx - 5, "y0": cy - 5, "x1": cx + 5, "y1": cy + 5,
            "cx": cx, "cy": cy}


def prepared_runner(monkeypatch, items, confirm=None):
    tr = make_runner(monkeypatch)
    tr._task_text = "nhắn cho Trường là tôi đến muộn"
    tr._raw_frame, tr._frame_hash = b"frame", "hash"
    tr._frame_at = runner.time.monotonic()
    tr._img_w, tr._img_h, tr._scr_w, tr._scr_h = 100, 50, 200, 100
    monkeypatch.setattr(grounding, "OCR_ENABLE", True)
    monkeypatch.setattr(grounding, "UIA_ENABLE", False)
    monkeypatch.setattr(grounding, "SAVE_IMAGES", False)

    async def fake_ocr(task_id):
        return items
    tr._ocr_items = fake_ocr
    tr.asked = []

    async def fake_confirm(task_id, name, args, message=""):
        tr.asked.append(message)
        return confirm
    tr._ask_confirm = fake_confirm
    return tr


TAP_NAME = {"target": "liên hệ Trường", "keywords": ["Trường"]}


def test_exact_name_clicks_without_asking(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [ocr_box("Tường", 10, 10), ocr_box("Trường")])
        result = await tr._tap("t", TAP_NAME)
        assert result.startswith("ok") and tr.asked == []
        assert tr.relay.calls == [("click", {"x": 100, "y": 40, "button": 1})]
    asyncio.run(go())


def test_lookalike_name_refused_does_not_click(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [ocr_box("Tường")], confirm=False)
        result = await tr._tap("t", TAP_NAME)
        assert "Tường" in tr.asked[0] and result.startswith("error")
        assert tr.relay.calls == []
    asyncio.run(go())


def test_lookalike_name_accepted_clicks(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [ocr_box("Tường")], confirm=True)
        assert (await tr._tap("t", TAP_NAME)).startswith("ok")
        assert len(tr.relay.calls) == 1
    asyncio.run(go())


def test_unreadable_name_found_by_vision_asks_first(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [ocr_box("Bình")], confirm=False)

        async def found(*a, **k):
            return 10, 10

        async def nothing(*a, **k):
            return None
        tr._locate = found
        tr._tap_marks = nothing
        tr._uia_marks = nothing
        result = await tr._tap("t", TAP_NAME)
        assert tr.asked and result.startswith("error") and tr.relay.calls == []
    asyncio.run(go())


def test_ui_label_is_not_treated_as_a_name(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [ocr_box("Gửi")])
        result = await tr._tap("t", {"target": "nút Gửi", "keywords": ["Gửi"]})
        assert result.startswith("ok") and tr.asked == []
    asyncio.run(go())


def test_named_anchor_lookalike_refused(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [ocr_box("Tường"), ocr_box("Aa", 50, 45)], confirm=False)
        result = await tr._tap("t", {"target": "ô nhập", "keywords": ["Aa"],
                                     "anchor": ["Trường"], "relation": "bottom_of"})
        assert tr.asked and result.startswith("error") and tr.relay.calls == []
    asyncio.run(go())


# --- relay frames ------------------------------------------------------------

def jpeg_frame(**extra):
    out = io.BytesIO()
    Image.new("RGB", (2, 2)).save(out, format="JPEG")
    return json.dumps({"type": "frame", "image": base64.b64encode(out.getvalue()).decode(),
                       "sw": 2, "sh": 2, **extra})


@pytest.fixture
def relay(monkeypatch):
    monkeypatch.setattr(ssl, "create_default_context", lambda **k: None)
    return RelayClient("wss://x", "token", "ca.pem")


def test_stale_tagged_frame_is_ignored(relay):
    async def go():
        waiter = relay.arm_frame("new")
        await relay._dispatch(jpeg_frame(request_id="old"))
        assert not waiter.done()
        await relay._dispatch(jpeg_frame(request_id="new"))
        assert waiter.result()
    asyncio.run(go())


def test_untagged_frame_still_works_with_current_firmware(relay):
    async def go():
        waiter = relay.arm_frame("any")
        await relay._dispatch(jpeg_frame())
        assert waiter.result()
    asyncio.run(go())


def test_arming_releases_the_previous_waiter(relay):
    async def go():
        first = relay.arm_frame("a")
        relay.arm_frame("b")
        assert first.result() is None
    asyncio.run(go())


def test_send_ui_survives_a_closing_socket(relay):
    class Closing:
        async def send(self, data):
            raise ConnectionError("closed")

    async def go():
        relay._ws = Closing()
        await relay.send_ui({"type": "log"})
    asyncio.run(go())
