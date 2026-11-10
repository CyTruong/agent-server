"""The Word skills load for the right requests and only use keys that exist."""
import re

import pytest

import skill_select
from tools import is_dangerous

ALL = {"overview", "open-file", "find-file", "close-file", "read-file", "create-file", "edit-file"}
# Keys the PC agent (Resolve-KeyToken) can press as the last key of a combo.
_LAST_KEY = r"([a-z0-9]|f([1-9]|1[0-2])|enter|tab|esc|home|end|up|down|left|right)"
_COMBO = re.compile(rf"((ctrl|alt|shift|win)\+)*{_LAST_KEY}")


def word_skills(text):
    _, names = skill_select.build_system_prompt(text)
    return {n.split("/")[1] for n in names if n.startswith("word/")}, names


@pytest.mark.parametrize("text,expected", [
    ("tìm file word báo cáo", "find-file"),
    ("mở file hop-dong.docx", "open-file"),
    ("đóng tài liệu word đang mở", "close-file"),
    ("đọc giúp tôi file word báo cáo", "read-file"),
    ("tạo tài liệu word mới về an toàn", "create-file"),
    ("sửa file word, thay 2024 thành 2025", "edit-file"),
])
def test_each_action_loads_its_skill(text, expected):
    loaded, _ = word_skills(text)
    assert expected in loaded and {"overview", "open-file"} <= loaded


@pytest.mark.parametrize("text", [
    "đổi password giúp tôi",
    "doc tin nhan zalo cua An",
    "nhắn zalo cho An nhớ mang tài liệu",
    "mở notepad",
])
def test_unrelated_requests_do_not_load_word(text):
    assert word_skills(text)[0] == set()


def test_generic_document_request_loads_word():
    assert word_skills("mở tài liệu báo cáo")[0]


def test_word_replaces_generic_windows_steps():
    _, names = word_skills("mở file word rồi sửa tiêu đề")
    assert "windows/open-app" not in names and "windows/text-editing" not in names


def test_all_skills_exist():
    assert {n.split("/")[1] for n in skill_select.ALL_SKILLS if n.startswith("word/")} == ALL


def test_prompt_with_every_action_stays_manageable():
    prompt, _ = skill_select.build_system_prompt(
        "tìm mở đọc sửa tạo mới rồi đóng file word")
    assert len(prompt) < 16000


def test_hotkeys_named_in_word_skills_are_known():
    text = "\n".join(v for k, v in skill_select.ALL_SKILLS.items() if k.startswith("word/"))
    combos = set(re.findall(r"`(?:hotkey )?((?:ctrl|alt|win|shift)\+[a-z0-9+]+|f\d+)", text))
    assert combos and all(_COMBO.fullmatch(c) for c in combos), combos


def test_hotkeys_run_without_confirmation():
    for combo in ("ctrl+f12", "f12", "ctrl+h", "ctrl+alt+1", "win+r", "win+e", "alt+f4"):
        assert not is_dangerous("hotkey", {"combo": combo})
