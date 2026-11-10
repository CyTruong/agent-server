"""Regressions from reviewing a real Zalo run, and UI-TARS ordering."""
import asyncio

import pytest

import runner
import task_cache
from matching import name_match
from test_runner import make_runner, reply, scripted_llm, tool_call


def step(tool, result="ok"):
    return {"tool": tool, "arguments": {}, "result": result}


@pytest.mark.parametrize("steps,expected", [
    ([step("screenshot"), step("tap"), step("type"), step("key")], False),
    ([step("key"), step("screenshot")], True),
    ([step("screenshot")], True),
    ([step("key"), step("screenshot", "error: x")], False),
    ([], False),
])
def test_verified_needs_a_screenshot_after_the_last_action(steps, expected):
    assert task_cache.is_verified_workflow(steps) is expected


def test_old_unverified_row_is_deleted_and_not_returned(tmp_path):
    cache = task_cache.TaskCache(tmp_path / "c.sqlite3")
    with cache._connect() as db:
        db.execute("INSERT INTO task_workflows_v2 (intent, skills, workflow, verified, created_at, "
                   "last_used_at) VALUES ('gui loi chao', '', "
                   "'[{\"tool\":\"screenshot\",\"arguments\":{},\"result\":\"ok\"},"
                   "{\"tool\":\"key\",\"arguments\":{},\"result\":\"ok\"}]', 1, 9e9, 9e9)")
    assert cache.retrieve("gui loi chao", []) is None
    with cache._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM task_workflows_v2").fetchone()[0] == 0


QUOTED = 'Lời chào "Chào A Vinh, bạn có khỏe không?" đã được gửi.'


def run_send(monkeypatch, shot_works=True):
    async def go():
        tr = make_runner(monkeypatch)

        async def fake_shot(task_id, *, force=False, attach=True):
            if shot_works:
                tr._pending_image = b"after"
            return "ok: đã chụp" if shot_works else "error: x"
        tr._screenshot = fake_shot
        seen = scripted_llm(monkeypatch, [
            reply("CO"),
            reply(calls=[tool_call("type", text="Xin chào")]),
            reply(calls=[{"function": {"name": "key", "arguments": {"name": "enter"}}}]),
            reply(QUOTED),
            reply("Đã thấy tin trên màn hình."),
        ])
        await tr.run_task("t", "gửi lời chào đến An trong zalo")
        return tr, seen
    return asyncio.run(go())


def test_send_is_checked_even_when_the_report_quotes_a_question(monkeypatch):
    tr, seen = run_send(monkeypatch)
    assert tr.relay.final()[-1]["result"] == "Đã thấy tin trên màn hình."
    assert tr._executed_steps[-1]["tool"] == "screenshot"
    assert any(isinstance(m, dict) and m.get("images") == [b"after"] for m in seen[-1]["messages"])


def test_failed_check_says_unverified_and_is_not_cached(monkeypatch):
    tr, _ = run_send(monkeypatch, shot_works=False)
    assert "chưa xác minh" in tr.relay.final()[-1]["result"]
    assert not task_cache.is_verified_workflow(tr._executed_steps)


def test_dropped_space_with_lost_accents_is_not_certain():
    assert name_match("Tô Ngân", "Tongan") == "accent"


def test_screen_cache_outlives_an_llm_turn():
    assert runner.SCREEN_CACHE_TTL >= 6
