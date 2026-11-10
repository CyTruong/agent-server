"""Tasks start from the real screen, and only relevant earlier requests are shown.
Reproduces t5: after reading a Word file, "send the summary to anh Vinh on Zalo"
acted as if Zalo were still in front."""
import asyncio

import runner
from test_region import region_runner
from test_runner import reply, scripted_llm

T2 = "Mở zalo và gửi lời chào đến anh vinh cho tôi"
T4 = "Mở văn bản Ho-so-du-an-bo-mach-may-ban-hang-tu-dong và tóm tắt nội dung"
T5 = "Gửi tóm tắt trên cho anh Vinh qua zalo"


def item(request, groups):
    return {"at": runner.time.monotonic(), "request": request, "result": "", "groups": groups}


# --- which earlier requests are relevant ------------------------------------------

def related(request, earlier, groups, named):
    return runner.TaskRunner._related(item(earlier, groups), request, named)


def test_same_app_is_related():
    assert related(T5, T2, ("zalo",), ("zalo",))


def test_referring_back_is_related():
    assert related(T5, T4, ("word",), ("zalo",))          # "tóm tắt TRÊN"


def test_other_app_without_reference_is_not():
    assert not related("mở facebook xem thông báo", T4, ("word",), ("facebook",))


def test_same_person_or_file_is_related():
    assert related("Gửi lời chào anh Vinh trên facebook", T2, ("zalo",), ("facebook",))
    assert related("đóng Ho-so-du-an-bo-mach-may-ban-hang-tu-dong trong word", T4,
                   ("word",), ("word",))


def test_follow_up_naming_no_app_keeps_context():
    assert related("sửa tiêu đề cho tôi", T4, ("word",), ())


# --- first look ------------------------------------------------------------------

def first_look_run(monkeypatch, title, request=T5, history=()):
    async def go():
        tr = region_runner(monkeypatch, text=title)
        monkeypatch.setattr(runner, "FIRST_LOOK", True)
        tr._history = list(history)
        seen = scripted_llm(monkeypatch, [reply("CO"), reply("xong"), reply("xong")])
        await tr.run_task("t", request)
        first = next(call for call in seen if call.get("tools"))
        images = [m for m in first["messages"] if isinstance(m, dict) and m.get("images")]
        return tr, images
    return asyncio.run(go())


def test_wrong_app_in_front_is_reported_before_the_first_step(monkeypatch):
    tr, images = first_look_run(monkeypatch, "Ho-so-du-an-bo-mach-may-ban-hang-tu-dong.docx - Word",
                                history=[item(T2, ("zalo",)), item(T4, ("word",))])
    assert len(images) == 1
    note = images[0]["content"]
    assert "Ho-so-du-an" in note and "Zalo) KHÔNG ở trước" in note
    shots = [c for c in tr.relay.calls if c[0] == "screenshot"]
    assert shots[0] == ("screenshot", {})                      # full screen
    assert shots[1][1]["region"][3] == 54                      # then the title bar


def test_right_app_in_front_is_confirmed(monkeypatch):
    _, images = first_look_run(monkeypatch, "Zalo - A Vinh")
    assert "(Zalo) đang ở trước" in images[0]["content"]


def test_plain_question_takes_no_screenshot(monkeypatch):
    async def go():
        tr = region_runner(monkeypatch)
        monkeypatch.setattr(runner, "FIRST_LOOK", True)
        scripted_llm(monkeypatch, [reply("Thủ đô là Hà Nội")])
        await tr.run_task("t", "thủ đô Việt Nam là gì")
        assert tr.relay.calls == []
    asyncio.run(go())


def test_unrelated_context_is_dropped(monkeypatch):
    async def go():
        tr = region_runner(monkeypatch)
        tr._history = [item(T4, ("word",))]
        seen = scripted_llm(monkeypatch, [reply("xong")])
        await tr.run_task("t", "mở facebook xem thông báo")
        contents = [str(m.get("content")) for m in seen[0]["messages"] if isinstance(m, dict)]
        assert not any(c.startswith("Ngữ cảnh") for c in contents)
        assert "[Word]" not in contents[0]
    asyncio.run(go())
