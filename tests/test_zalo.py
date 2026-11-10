"""Regressions found by walking the Zalo skills through the code."""
import asyncio
import glob
import os
import unicodedata

import pytest

import grounding
import runner
import skill_select
import task_cache
from matching import is_proper_name
from test_runner import make_runner
from tools import TOOLS, split_for_type


@pytest.mark.parametrize("keyword,request_text,target", [
    ("Trường", "nhan zalo cho truong", "kết quả tìm kiếm Trường"),
    ("Trường", "mở zalo", "cuộc trò chuyện với Trường trong danh sách bên trái"),
])
def test_zalo_targets_are_checked_as_names(keyword, request_text, target):
    assert is_proper_name(keyword, request_text, target)


def test_input_placeholder_is_not_a_name():
    assert not is_proper_name("tin nhắn tới", "nhắn zalo cho An",
                              "ô nhập tin nhắn ở đáy khung trò chuyện")


def test_cached_hint_has_no_old_recipient():
    match = {"score": 0.98, "workflow": [{"tool": "tap", "arguments": {
        "target": "liên hệ Trường", "keywords": ["Trường"], "area": "left"}}]}
    hint = task_cache.TaskCache.prompt_hint(match)
    assert "Trường" not in hint and "left" in hint


def test_new_cache_rows_have_no_names():
    step = task_cache._safe_step("tap", {"target": "An", "keywords": ["An"], "area": "left"}, "ok")
    assert step["arguments"] == {"area": "left"}


LONG = ("Mai anh qua nhà em lấy tài liệu dự án nhé, nhớ chuẩn bị sẵn giúp anh, "
        "cảm ơn em nhiều lắm 👍 " * 4).strip()


@pytest.mark.parametrize("text", [
    "xin chào", LONG, "a" * 500, "ệ" * 200, ("👨‍👩‍👧 gia đình " * 20).strip(),
    unicodedata.normalize("NFD", "Trường " * 40).strip(),
])
def test_split_round_trips_and_fits(text):
    chunks = split_for_type(text)
    assert "".join(chunks) == unicodedata.normalize("NFC", text)
    for chunk in chunks:
        assert len(chunk.encode()) <= 90
        assert chunk == chunk.strip()              # the PC agent trims each command
        assert not unicodedata.combining(chunk[0])


def test_split_refuses_when_no_safe_cut():
    with pytest.raises(ValueError):
        split_for_type("a " * 3, max_bytes=1)


def test_long_type_sends_chunks_in_order(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        result = await tr._run_tool("t", "type", {"text": LONG})
        typed = [args["text"] for tool, args in tr.relay.calls]
        assert len(typed) > 1 and "".join(typed) == LONG and result.startswith("ok")
    asyncio.run(go())


def test_partial_type_warns_not_to_press_enter(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        tr.relay.device_error = asyncio.TimeoutError()
        result = await tr._run_tool("t", "type", {"text": LONG})
        assert len(tr.relay.calls) == 1 and "ĐỪNG nhấn Enter" in result
    asyncio.run(go())


def test_tap_works_without_keywords_in_schema():
    tap = next(t for t in TOOLS if t["function"]["name"] == "tap")
    assert tap["function"]["parameters"]["required"] == ["target"]


@pytest.mark.parametrize("request_text", ["An nhắn gì trên zalo", "xem zalo của An"])
def test_reading_requests_load_read_skill(request_text):
    _, names = skill_select.build_system_prompt(request_text)
    assert "zalo/read-chat" in names


def test_active_skills_do_not_ask_before_sending():
    root = os.path.dirname(os.path.abspath(runner.__file__))
    for group in ("zalo", "facebook", "common"):
        for path in glob.glob(os.path.join(root, "skills", group, "*", "SKILL.md")):
            with open(path, encoding="utf-8") as f:
                assert "xin xác nhận" not in f.read().lower(), path


def test_grounding_module_still_exports_the_mixin():
    assert hasattr(grounding, "GroundingMixin")
