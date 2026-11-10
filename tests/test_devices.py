"""Several devices on one agent, and context between consecutive requests."""
import asyncio
import base64
import io
import json
import ssl

from PIL import Image

import agent_log
import llm
import runner
import sessions
import skill_select
import task_cache
from relay_client import RelayClient
from test_runner import make_runner, reply, scripted_llm, tool_call


# --- RelayClient: frames and calls are kept per device -------------------------

def relay_client(monkeypatch):
    monkeypatch.setattr(ssl, "create_default_context", lambda **k: None)
    return RelayClient("wss://x", "token", "ca.pem")


def frame(device_id, sw=1920):
    out = io.BytesIO()
    Image.new("RGB", (2, 2)).save(out, format="JPEG")
    return json.dumps({"type": "frame", "device_id": device_id, "sw": sw, "sh": 1080,
                       "image": base64.b64encode(out.getvalue()).decode()})


def test_a_frame_only_reaches_its_device(monkeypatch):
    relay = relay_client(monkeypatch)

    async def go():
        a, b = relay.arm_frame("ra", device_id="pc-a"), relay.arm_frame("rb", device_id="pc-b")
        await relay._dispatch(frame("pc-b", sw=1366))
        assert b.result() and not a.done()
        assert relay.frame_size("pc-b") == (1366, 1080) and relay.frame_size("pc-a") == (0, 0)
        await relay._dispatch(frame("pc-a"))
        assert a.result()
    asyncio.run(go())


def test_arming_releases_only_the_same_device(monkeypatch):
    relay = relay_client(monkeypatch)

    async def go():
        a = relay.arm_frame("r1", device_id="pc-a")
        relay.arm_frame("r2", device_id="pc-b")
        assert not a.done()
        relay.arm_frame("r3", device_id="pc-a")
        assert a.result() is None
    asyncio.run(go())


def test_device_call_is_addressed(monkeypatch):
    relay = relay_client(monkeypatch)
    sent = []

    class Socket:
        async def send(self, data):
            sent.append(json.loads(data))
            await relay._dispatch(json.dumps({"type": "device_result", "ok": True,
                                              "request_id": sent[-1]["request_id"]}))

    async def go():
        relay._ws = Socket()
        await relay.call_device("click", {"x": 1, "y": 2}, "t1", device_id="pc-a")
        assert sent[0]["device_id"] == "pc-a"
    asyncio.run(go())


# --- SessionManager: one runner per device --------------------------------------

class Hub:
    """Stands in for RelayClient: records what each device would receive."""

    def __init__(self):
        self.ui, self.calls, self.waiters = [], [], {}

    async def send_ui(self, msg):
        self.ui.append(msg)

    async def call_device(self, tool, arguments, task_id, timeout=10.0, request_id="",
                          device_id=""):
        self.calls.append((device_id, tool))
        return {"ok": True, "result": {"detail": ""}}

    def arm_frame(self, request_id="", device_id=""):
        return asyncio.get_running_loop().create_future()

    def frame_size(self, device_id=""):
        return (0, 0)

    def for_device(self, device_id, kind=None):
        return [m for m in self.ui if m.get("device_id") == device_id
                and (kind is None or m["type"] == kind)]


def manager(monkeypatch, ui_logs=False):
    monkeypatch.setattr(sessions, "UI_LOGS", ui_logs)
    monkeypatch.setattr(runner, "CACHE_ENABLED", False)
    monkeypatch.setattr(runner, "PLAN_ENABLE", False)
    monkeypatch.setattr(runner, "FIRST_LOOK", False)   # Hub never delivers frames
    hub = Hub()
    return hub, sessions.SessionManager(hub)


async def wait_until(condition, rounds=200):
    for _ in range(rounds):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("timed out")


def test_two_devices_run_at_the_same_time(monkeypatch):
    async def go():
        hub, mgr = manager(monkeypatch)
        release = asyncio.Event()

        async def chat(**kwargs):
            text = kwargs["messages"][1]["content"]   # the request
            if not any(m.get("role") == "tool" for m in kwargs["messages"]):
                return reply(calls=[tool_call("scroll", amount=-1)])
            await release.wait()
            return reply("xong " + text.split()[0])
        monkeypatch.setattr(llm, "chat", chat)
        await mgr.on_chat({"type": "chat", "device_id": "pc-a", "task_id": "t1", "text": "an"})
        await mgr.on_chat({"type": "chat", "device_id": "pc-b", "task_id": "t1", "text": "binh"})
        await wait_until(lambda: len(hub.calls) == 2)
        assert sorted(hub.calls) == [("pc-a", "scroll"), ("pc-b", "scroll")]
        assert not [m for m in hub.ui if m.get("error") == "một tác vụ khác đang chạy"]
        release.set()
        await asyncio.gather(*(s._active for s in mgr.sessions.values()))
        assert hub.for_device("pc-a", "done")[0]["result"].startswith("xong an")
        assert hub.for_device("pc-b", "done")[0]["result"].startswith("xong binh")
    asyncio.run(go())


def test_an_answer_from_another_device_is_a_new_task_there(monkeypatch):
    async def go():
        hub, mgr = manager(monkeypatch)
        a = mgr.session("pc-a")
        a._choice = asyncio.get_running_loop().create_future()
        scripted_llm(monkeypatch, [reply("Chào")])
        await mgr.on_chat({"type": "chat", "device_id": "pc-b", "task_id": "t9", "text": "2"})
        await mgr.sessions["pc-b"]._active
        assert not a._choice.done()
        await mgr.on_chat({"type": "chat", "device_id": "pc-a", "task_id": "t2", "text": "2"})
        assert a._choice.result() == "2"
    asyncio.run(go())


def test_logs_go_to_the_device_of_the_task(monkeypatch):
    async def go():
        hub, mgr = manager(monkeypatch, ui_logs=True)
        scripted_llm(monkeypatch, [reply("ok")])
        await mgr.on_chat({"type": "chat", "device_id": "pc-a", "task_id": "t1", "text": "hi"})
        await mgr.sessions["pc-a"]._active
        await wait_until(lambda: hub.for_device("pc-a", "log"))
        assert not [m for m in hub.ui if m["type"] == "log" and m.get("device_id") != "pc-a"]
        agent_log._log_q = None
    asyncio.run(go())


def test_workflow_cache_is_per_device(tmp_path):
    cache = task_cache.TaskCache(tmp_path / "c.sqlite3")
    steps = [{"tool": "key", "arguments": {}, "result": "ok"},
             {"tool": "screenshot", "arguments": {}, "result": "ok"}]
    cache.record_verified("mo file bao cao", [], steps, device_id="pc-a")
    assert cache.retrieve("mo file bao cao", [], device_id="pc-b") is None
    assert cache.retrieve("mo file bao cao", [], device_id="pc-a")


# --- Context between requests ---------------------------------------------------

READ = "cho tôi biết nội dung của Ho-so-du-an-bo-mach-may-ban-hang-tu-dong cho tôi ( word )"
EDIT = "thay đổi Nhà văn hành máy bán hàng tự động quy mô lớn tại TP.HCM từ TP HCM thành Hà Nội"


def has_context(messages):
    return any(isinstance(m, dict) and str(m.get("content", "")).startswith("Ngữ cảnh")
               for m in messages)


def run_two(monkeypatch, first=READ, second=EDIT):
    async def go():
        tr = make_runner(monkeypatch)
        seen = scripted_llm(monkeypatch, [reply("Nội dung: ... tại TP.HCM"), reply("Đã sửa")])
        await tr.run_task("t3", first)
        await tr.run_task("t4", second)
        return tr, seen[-1]["messages"]
    return asyncio.run(go())


def test_follow_up_sees_the_previous_request_and_keeps_word_skills(monkeypatch):
    _, messages = run_two(monkeypatch)
    assert "[Word] Chỉnh sửa" in messages[0]["content"]
    assert "Ho-so-du-an" in messages[1]["content"] and "TP.HCM" in messages[1]["content"]
    assert messages[2]["content"].startswith(EDIT)


def test_naming_another_app_drops_the_carried_skills(monkeypatch):
    _, messages = run_two(monkeypatch, second="nhắn zalo cho An là đến muộn")
    assert "[Zalo]" in messages[0]["content"] and "[Word]" not in messages[0]["content"]


def test_old_context_expires(monkeypatch):
    monkeypatch.setattr(runner, "CONTEXT_TTL", 0)
    _, messages = run_two(monkeypatch)
    assert not has_context(messages) and "[Word]" not in messages[0]["content"]


def test_reset_command_clears_the_context(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        seen = scripted_llm(monkeypatch, [reply("Nội dung"), reply("Đã sửa")])
        await tr.run_task("t3", READ)
        await tr.on_chat({"text": "Bắt đầu lại", "task_id": "t4"})
        assert tr.relay.final()[-1]["result"].startswith("Đã xoá ngữ cảnh")
        await tr.run_task("t5", EDIT)
        assert not has_context(seen[-1]["messages"])
    asyncio.run(go())


def test_devices_do_not_share_context(monkeypatch):
    async def go():
        hub, mgr = manager(monkeypatch)
        seen = scripted_llm(monkeypatch, [reply("Nội dung"), reply("Đã sửa"), reply("Đã sửa")])
        await mgr.session("pc-a").run_task("t3", READ)
        await mgr.session("pc-b").run_task("t3", EDIT)
        assert not has_context(seen[-1]["messages"])
        await mgr.session("pc-a").run_task("t4", EDIT)
        assert has_context(seen[-1]["messages"])
    asyncio.run(go())


def test_carry_only_applies_when_no_app_is_named():
    _, names = skill_select.build_system_prompt(EDIT, carry=("word",))
    assert "word/edit-file" in names
    _, names = skill_select.build_system_prompt("mở facebook", carry=("word",))
    assert not any(n.startswith("word/") for n in names)
