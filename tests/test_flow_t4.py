"""Regressions from reviewing a real Zalo run, and UI-TARS ordering."""
import asyncio
import io

import pytest
from PIL import Image

import grounding
import llm
import ocr
import runner
import task_cache
from matching import name_match, resolve_name
from test_runner import make_runner, ocr_box, prepared_runner, reply, scripted_llm, tool_call


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
        db.execute("INSERT INTO task_workflows (intent, skills, workflow, verified, created_at, "
                   "last_used_at) VALUES ('gui loi chao', '', "
                   "'[{\"tool\":\"screenshot\",\"arguments\":{},\"result\":\"ok\"},"
                   "{\"tool\":\"key\",\"arguments\":{},\"result\":\"ok\"}]', 1, 9e9, 9e9)")
    assert cache.retrieve("gui loi chao", []) is None
    with cache._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM task_workflows").fetchone()[0] == 0


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


def test_dropped_space_is_the_same_name():
    assert name_match("A Vinh", "AVinh") == "exact"
    assert resolve_name(["A Vinh"], [{"text": "AVinh", "cx": 1, "cy": 1}]).status == "certain"


def test_dropped_space_with_lost_accents_is_not_certain():
    assert name_match("Tô Ngân", "Tongan") == "accent"


HEADER = ocr_box("Trường", 500, 25)
LIST_ROW = ocr_box("Trường", 120, 80)
FIELD = ocr_box("tin nhắn tới Bình", 500, 570)
ZALO_TAP = {"target": "ô nhập", "keywords": ["tin nhắn tới"], "anchor": ["Trường"],
            "relation": "bottom_of", "area": "bottom"}


def zalo_runner(monkeypatch, items, confirm=False):
    tr = prepared_runner(monkeypatch, items, confirm=confirm)
    tr._task_text = "nhắn zalo cho Trường"
    tr._img_w, tr._img_h, tr._scr_w, tr._scr_h = 1000, 600, 1000, 600
    monkeypatch.setattr(grounding, "UITARS_BEFORE_OCR", False)
    return tr


def test_zalo_list_row_alone_does_not_prove_the_open_chat(monkeypatch):
    async def go():
        tr = zalo_runner(monkeypatch, [LIST_ROW, FIELD])

        async def none(*a, **k):
            return None
        tr._uia_marks = tr._tap_marks = tr._locate = none
        await tr._tap("t", dict(ZALO_TAP))
        assert tr.asked and tr.relay.calls == []
    asyncio.run(go())


def test_zalo_header_is_accepted(monkeypatch):
    async def go():
        tr = zalo_runner(monkeypatch, [LIST_ROW, HEADER, FIELD])
        assert (await tr._tap("t", dict(ZALO_TAP))).startswith("ok")
        assert tr.relay.calls == [("click", {"x": 500, "y": 570, "button": 1})] and not tr.asked
    asyncio.run(go())


def frame_bytes():
    out = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(out, format="PNG")
    return out.getvalue()


NAME_TAP = {"target": "liên hệ Trường", "keywords": ["Trường"]}


def reread_runner(monkeypatch, ocr_text, vision_text=""):
    tr = prepared_runner(monkeypatch, [ocr_box("Trudng")], confirm=False)
    tr._raw_frame = frame_bytes()
    monkeypatch.setattr(ocr, "run_ocr", lambda *a: [{"text": ocr_text}] if ocr_text else [])
    tr.vision_calls = []

    async def fake_chat(**kwargs):
        tr.vision_calls.append(kwargs["messages"][0]["content"])
        return reply(vision_text)
    monkeypatch.setattr(llm, "chat", fake_chat)
    return tr


def test_reread_by_ocr_avoids_asking(monkeypatch):
    async def go():
        tr = reread_runner(monkeypatch, "Trường")
        assert (await tr._tap("t", dict(NAME_TAP))).startswith("ok")
        assert not tr.asked and not tr.vision_calls
    asyncio.run(go())


def test_reread_by_vision_avoids_asking_and_hides_the_name(monkeypatch):
    async def go():
        tr = reread_runner(monkeypatch, "Trudng", "Trường")
        assert (await tr._tap("t", dict(NAME_TAP))).startswith("ok")
        assert not tr.asked and "Trường" not in tr.vision_calls[0]
    asyncio.run(go())


def test_reread_of_another_name_still_asks(monkeypatch):
    async def go():
        tr = reread_runner(monkeypatch, "Tường", "Tường")
        assert (await tr._tap("t", dict(NAME_TAP))).startswith("error")
        assert tr.asked and tr.relay.calls == []
    asyncio.run(go())


def tars_runner(monkeypatch, point, before=True):
    tr = prepared_runner(monkeypatch, [ocr_box("Gửi", 60, 30)], confirm=False)
    monkeypatch.setattr(grounding, "UITARS_BEFORE_OCR", before)

    async def fake_tars(*a, **k):
        tr.tars_called = True
        return point
    tr.tars_called = False
    tr._tars_ground = fake_tars
    return tr


def test_tars_runs_before_ocr(monkeypatch):
    async def go():
        tr = tars_runner(monkeypatch, (10, 12))
        result = await tr._tap("t", {"target": "nút Gửi", "keywords": ["Gửi"]})
        assert "ui-tars" in result and tr.relay.calls[0][1]["x"] == 10
    asyncio.run(go())


def test_ocr_takes_over_when_tars_has_no_answer(monkeypatch):
    async def go():
        tr = tars_runner(monkeypatch, None)
        result = await tr._tap("t", {"target": "nút Gửi", "keywords": ["Gửi"]})
        assert tr.tars_called and "OCR-exact" in result
    asyncio.run(go())


def test_switch_restores_ocr_first(monkeypatch):
    async def go():
        tr = tars_runner(monkeypatch, (10, 12), before=False)
        result = await tr._tap("t", {"target": "nút Gửi", "keywords": ["Gửi"]})
        assert "OCR-exact" in result and not tr.tars_called
    asyncio.run(go())


def test_name_check_still_precedes_tars(monkeypatch):
    async def go():
        tr = tars_runner(monkeypatch, (10, 12))
        tr._ocr_items = None

        async def items(task_id):
            return [ocr_box("Trường")]
        tr._ocr_items = items
        await tr._tap("t", dict(NAME_TAP))
        assert not tr.tars_called and tr.relay.calls[0][1]["x"] == 100
    asyncio.run(go())


def test_tars_point_outside_the_anchor_region_is_refused(monkeypatch):
    async def go():
        tr = zalo_runner(monkeypatch, [HEADER, FIELD])
        monkeypatch.setattr(grounding, "UITARS_BEFORE_OCR", True)

        async def wrong(*a, **k):
            return 120, 80
        tr._tars_ground = wrong
        await tr._tap("t", dict(ZALO_TAP))
        assert tr.relay.calls == [("click", {"x": 500, "y": 570, "button": 1})]
    asyncio.run(go())


def test_screen_cache_outlives_an_llm_turn():
    assert runner.SCREEN_CACHE_TTL >= 6
