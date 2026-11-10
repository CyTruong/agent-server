"""ask_user: the task asks, waits for the chat reply, and carries on."""
import asyncio

import pytest

import runner
from intents import parse_choice
from matching import is_proper_name
from test_runner import make_runner, reply, scripted_llm, tool_call
from tools import is_dangerous

FILES = ["Ho-so-du-an-bo-mach.docx — 20 ngày trước", "Ho-so-du-an-cu.docx — 2 năm trước"]


def run_with_reply(monkeypatch, answer, delay=0.05, timeout=None):
    async def go():
        tr = make_runner(monkeypatch)
        if timeout is not None:
            monkeypatch.setattr(runner, "TASK_TIMEOUT", timeout)
        seen = scripted_llm(monkeypatch, [
            reply(calls=[tool_call("ask_user", question="Mở file nào?", options=FILES)]),
            reply("Đã mở"),
        ])
        await tr.on_chat({"text": "mở file word hồ sơ dự án", "task_id": "t1"})
        for _ in range(100):
            await asyncio.sleep(0.01)
            if any(m.get("options") for m in tr.relay.ui):
                break
        await asyncio.sleep(delay)
        await tr.on_chat({"text": answer, "task_id": "t2"})
        await asyncio.wait_for(tr._active, 2)
        tool = [m for m in seen[-1]["messages"] if m.get("role") == "tool"][0]["content"]
        return tr, tool
    return asyncio.run(go())


def test_reply_by_number_is_the_answer_not_a_new_task(monkeypatch):
    tr, tool = run_with_reply(monkeypatch, "2")
    assert tool == f"ok: người dùng chọn 2. {FILES[1]}"
    assert tr.relay.final()[-1]["result"] == "Đã mở"
    assert not any(m.get("error") == "một tác vụ khác đang chạy" for m in tr.relay.ui)


def test_question_lists_numbered_options(monkeypatch):
    tr, _ = run_with_reply(monkeypatch, "1")
    question = next(m for m in tr.relay.ui if m.get("options"))
    assert question["type"] == "assistant" and "2. Ho-so-du-an-cu" in question["content"]


def test_free_text_reply_is_passed_on(monkeypatch):
    _, tool = run_with_reply(monkeypatch, "cái mới nhất")
    assert tool == "ok: người dùng trả lời: cái mới nhất"


def test_waiting_for_the_user_does_not_use_up_the_task_time(monkeypatch):
    tr, _ = run_with_reply(monkeypatch, "1", delay=0.4, timeout=0.3)
    assert tr.relay.final()[-1]["result"] == "Đã mở"


def test_no_answer_tells_the_model_not_to_guess(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        monkeypatch.setattr(runner, "CHOICE_TIMEOUT", 0.05)
        result = await tr._run_tool("t", "ask_user", {"question": "Mở file nào?", "options": FILES})
        assert result.startswith("error") and "ĐỪNG tự chọn" in result
        assert tr._choice is None
    asyncio.run(go())


@pytest.mark.parametrize("answer,expected", [
    ("2", 1), ("số 1", 0), ("Chọn 2.", 1), ("3", None), ("Zalo", 0), ("không biết", None),
])
def test_parse_choice(answer, expected):
    assert parse_choice(answer, ["Zalo", "Facebook"]) == expected


def test_windows_search_runs_without_confirmation():
    assert not is_dangerous("hotkey", {"combo": "win+s"})


@pytest.mark.parametrize("keyword,target", [
    ("Ho-so-du-an-bo-mach-may-ban-hang", "dòng file Word Ho-so-du-an"),
    ("Hồ Sơ Dự Án", "dòng file Word Hồ Sơ Dự Án"),
])
def test_file_names_are_not_treated_as_people(keyword, target):
    assert not is_proper_name(keyword, f"mở file {keyword}", target)
