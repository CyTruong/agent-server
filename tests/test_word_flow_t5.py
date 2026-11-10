"""Regressions from run t5: the edit skill was not loaded and scrolling looped."""
import asyncio
import io

import pytest
from PIL import Image, ImageDraw

import runner
import skill_select
from progress import ProgressWatch
from test_runner import make_runner, reply, scripted_llm, tool_call

T5 = "bồ sung mục ký xác nhận ở cuối file cho tôi"


def skills(text, carry=()):
    return skill_select.build_system_prompt(text, carry)


def test_follow_up_with_a_tone_typo_loads_the_edit_skill():
    prompt, names = skills(T5, carry=("word",))
    assert {"word/edit-file", "word/view"} <= set(names)
    assert "Mục ký xác nhận" in prompt


def test_accent_folding_does_not_confuse_single_syllables():
    names = skills("chỉnh giao diện file word")[1]   # "diện" is not "điền"
    assert "windows/web-forms" not in names and "word/view" in names


@pytest.mark.parametrize("text,expected", [
    ("sua file word bao cao", "word/edit-file"),
    ("chuyển từ hai trang sang một trang trong word", "word/view"),
    ("đọc file word hợp đồng", "word/view"),
])
def test_skill_keywords(text, expected):
    assert expected in skills(text)[1]


def test_follow_up_without_action_words_gets_edit_and_read():
    names = skills("cái đó thì sao", carry=("word",))[1]
    assert {"word/edit-file", "word/read-file"} <= set(names)


@pytest.mark.parametrize("text", [
    "soạn công văn gửi phòng kế toán",
    "tạo biên bản họp hôm nay",
    "sửa hợp đồng thuê nhà",
    "mở văn bản hành chính số 12",
])
def test_document_words_turn_word_on(text):
    assert any(n.startswith("word/") for n in skills(text)[1])


@pytest.mark.parametrize("text", [
    "gõ văn bản vào notepad",
    "nhắn zalo cho An gửi biên bản",
    "mở báo cáo excel tháng 5",
])
def test_document_words_with_another_app_do_not(text):
    assert not any(n.startswith("word/") for n in skills(text)[1])


def test_prompt_with_every_word_action_stays_bounded():
    prompt, _ = skills("tìm mở đọc sửa tạo mới rồi đóng file word, chỉnh giao diện")
    assert len(prompt) < 21500


def test_typical_edit_prompt_stays_bounded():
    # Raise only on purpose: ~18k characters is already about 6k tokens of the
    # model's context before any screenshot.
    prompt, _ = skills(T5, carry=("word",))
    assert len(prompt) < 18500


# --- stuck detection -------------------------------------------------------------

def jpeg(shift=0):
    image = Image.new("RGB", (320, 180), "white")
    ImageDraw.Draw(image).rectangle([20, 20 + shift, 300, 60 + shift], fill="black")
    out = io.BytesIO()
    image.save(out, format="JPEG")
    return out.getvalue()


def test_watch_warns_only_when_an_action_changed_nothing():
    watch = ProgressWatch(3)
    assert watch.screenshot(jpeg()) == ""
    assert watch.screenshot(jpeg()) == ""            # no action in between
    watch.action("scroll")
    assert "KHÔNG thay đổi sau scroll" in watch.screenshot(jpeg())
    watch.action("scroll")
    assert watch.screenshot(jpeg(shift=100)) == "" and watch.stuck == 0


def test_watch_is_exhausted_after_the_limit():
    watch = ProgressWatch(2)
    watch.screenshot(jpeg())
    for _ in range(2):
        watch.action("scroll")
        watch.screenshot(jpeg())
    assert watch.exhausted


def run_scrolls(monkeypatch, moving=False, steps=None):
    async def go():
        tr = make_runner(monkeypatch)
        if steps:
            monkeypatch.setattr(runner, "MAX_STEPS", steps)
        shots = iter(range(100))

        async def fake_shot(task_id, *, force=False, attach=True):
            tr._raw_frame = jpeg(shift=next(shots) * 40 % 120 if moving else 0)
            return "ok: đã chụp màn hình"
        tr._screenshot = fake_shot
        replies = [reply(calls=[tool_call("screenshot")])]
        for _ in range(10):
            replies += [reply(calls=[tool_call("scroll", amount=-5)]),
                        reply(calls=[tool_call("screenshot")])]
        seen = scripted_llm(monkeypatch, replies)
        await tr.run_task("t5", "đọc file word báo cáo")
        return tr, seen
    return asyncio.run(go())


def test_scrolling_that_changes_nothing_stops_the_task(monkeypatch):
    tr, seen = run_scrolls(monkeypatch)
    final = tr.relay.final()[-1]["result"]
    assert final.startswith("Dừng vì bị kẹt") and "scroll(amount=-5)" in final
    assert len(seen) == 7                       # stopped after the 3rd still screenshot
    warnings = [m["content"] for m in seen[-1]["messages"]
                if isinstance(m, dict) and "CẢNH BÁO" in str(m.get("content"))]
    assert len(warnings) == 3


def test_scrolling_that_moves_the_page_is_not_stuck(monkeypatch):
    tr, _ = run_scrolls(monkeypatch, moving=True, steps=6)
    final = tr.relay.final()[-1]["result"]
    assert final.startswith("Đạt giới hạn 6 bước") and "Các bước cuối" in final


def test_editing_brings_the_find_related_fallback():
    prompt, names = skills("sửa đoạn cam kết trong file word")
    assert {"word/edit-file", "word/find-related"} <= set(names)
    assert "Không có kết quả" in prompt and "vừa đọc vừa tìm" in prompt


def test_describing_content_loads_find_related():
    names = skills("tìm đoạn nói về ngân sách trong file word")[1]
    assert "word/find-related" in names
