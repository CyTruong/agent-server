"""TaskRunner and RelayClient behaviour with a fake relay and a fake LLM."""
import asyncio
import base64
import io
import json
import ssl

import pytest
from PIL import Image

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
    monkeypatch.setattr(runner, "CACHE_ENABLED", False)
    monkeypatch.setattr(runner, "PLAN_ENABLE", False)
    monkeypatch.setattr(runner, "FIRST_LOOK", False)   # FakeRelay cannot take shots
    monkeypatch.setattr(runner, "AUTO_READ_PAGES", 0)  # tests that cover it turn it on
    tr = runner.TaskRunner(relay or FakeRelay())

    # Reading the screen goes through the vision model, which would spend the
    # scripted replies; tests that cover reading replace these.
    async def no_transcript(task_id):
        return None

    async def no_top_result(task_id):
        return ""
    tr._transcribe_page = no_transcript
    tr._top_search_result = no_top_result
    return tr


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
