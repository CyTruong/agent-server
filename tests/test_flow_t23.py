"""Regressions from run t23: guessed path, short keyword clicks, Enter in search."""
import asyncio

import grounding
import skill_select
from matching import _sim
from test_runner import make_runner, ocr_box, prepared_runner, reply, scripted_llm, tool_call

T23 = "Mở tài liệu Ho-so-du-an-bo-mach-may-ban-hang-tu-dong và tóm tắt cho tôi"


# A: short keywords and the top area -----------------------------------------------

def test_short_keyword_does_not_match_a_similar_word():
    assert _sim("Dọc", "Toc dộ giao dịch") < 0.8
    assert _sim("Dọc", "Dọc") == 1.0 and _sim("Gửi", "Gui") == 1.0
    assert _sim("Vertical", "Verticai") >= 0.8          # longer words still fuzzy


def test_top_target_is_not_taken_from_page_content(monkeypatch):
    async def go():
        tr = prepared_runner(monkeypatch, [ocr_box("Dọc", 50, 40)])   # y=40 of 50: page
        pt = await tr._tap_ocr("t", "nút Vertical", ["Dọc"], "top",
                               await tr._ocr_items("t"), thr=0.8)
        assert pt is None
        pt = await tr._tap_ocr("t", "nút Vertical", ["Dọc"], "center",
                               [ocr_box("Dọc", 50, 40)], thr=0.8)
        assert pt == (50, 40)                           # other areas only rank
    asyncio.run(go())


# B: Enter in Windows Search ---------------------------------------------------------

def search_run(monkeypatch, after_type):
    async def go():
        tr = make_runner(monkeypatch)
        shots = []

        async def fake_shot(task_id, *, force=False, attach=True):
            shots.append(attach)
            tr._pending_image = b"results"
            return "ok: đã chụp"
        tr._screenshot = fake_shot
        seen = scripted_llm(monkeypatch, [
            reply(calls=[tool_call("hotkey", combo="win+s")]),
            reply(calls=[tool_call("type", text="Ho-so")]),
            *after_type,
            reply("xong"),
        ])
        await tr.run_task("t", "mở file word Ho-so")
        return tr, seen, shots
    return asyncio.run(go())


ENTER = reply(calls=[{"function": {"name": "key", "arguments": {"name": "enter"}}}])


def test_enter_before_looking_is_replaced_by_a_screenshot(monkeypatch):
    tr, seen, shots = search_run(monkeypatch, [ENTER, ENTER])
    sent = [call[0] for call in tr.relay.calls]
    assert sent == ["hotkey", "type", "key"]          # only the second Enter went out
    tools = [m for m in seen[-1]["messages"] if isinstance(m, dict) and m.get("role") == "tool"]
    assert "CHƯA nhấn Enter" in tools[2]["content"]
    assert any(isinstance(m, dict) and m.get("images") == [b"results"] for m in seen[-1]["messages"])


def test_enter_after_a_screenshot_goes_through(monkeypatch):
    shot = reply(calls=[tool_call("screenshot")])
    tr, _, _ = search_run(monkeypatch, [shot, ENTER])
    assert [call[0] for call in tr.relay.calls] == ["hotkey", "type", "key"]


# C: no cache after getting stuck ----------------------------------------------------

def test_stuck_task_is_not_cached(monkeypatch):
    tr = make_runner(monkeypatch)
    saved = []
    tr.task_cache.record_verified = lambda *a, **k: saved.append(a)
    tr._executed_steps = [{"tool": "key", "arguments": {}, "result": "ok"},
                          {"tool": "screenshot", "arguments": {}, "result": "ok"}]
    tr._stuck_warned = True
    tr._record_verified_cache("x", [])
    tr._stuck_warned = False
    tr._record_verified_cache("x", [])
    assert len(saved) == 1


# D: whole-word skill keywords -------------------------------------------------------

def test_summarise_does_not_load_the_close_skill():
    names = skill_select.build_system_prompt(T23)[1]
    assert "word/read-file" in names and "word/close-file" not in names
    assert "word/close-file" in skill_select.build_system_prompt("tắt file word đi")[1]


# E: Word opens only through Windows Search ------------------------------------------

def test_open_skill_never_uses_paths_or_the_open_dialog():
    skills = skill_select.ALL_SKILLS
    for name in ("word/open-file", "word/find-file", "word/overview"):
        assert "hotkey ctrl+f12" not in skills[name], name
        assert "hotkey win+r" not in skills[name], name
    assert "KHÔNG BAO GIỜ tự đặt ra đường dẫn" in skills["word/open-file"]


def test_grounding_still_imports():
    assert hasattr(grounding, "GroundingMixin")
