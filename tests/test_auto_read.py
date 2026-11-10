"""Run t6: the model summarised the one screen it saw (section 4 onwards) without
going to the top or scrolling. The agent now reads the document itself."""
import asyncio

import runner
from test_loops import jpeg
from test_runner import make_runner, reply, scripted_llm, tool_call

T6 = "Mở tài liệu Ho-so-du-an-bo-mach-may-ban-hang-tu-dong và tóm tắt cho tôi"
# What the model copies from each screen the agent captures, in order. Screen 0 is
# where the model happened to be (section 4); the agent then goes to the top.
SCREENS = ["4. Bài toán ban đầu: bo mạch cũ hay lỗi",
           "1. Tên dự án: Bo mạch máy bán hàng\n2. Khách hàng: bán lẻ",
           "3. Tiến độ: 6 tháng\n4. Bài toán ban đầu: bo mạch cũ hay lỗi",
           "8. Khó khăn: nhiễu nguồn",
           "8. Khó khăn: nhiễu nguồn",
           "8. Khó khăn: nhiễu nguồn"]


def run_t6(monkeypatch, pages=15, screens=SCREENS):
    async def go():
        tr = make_runner(monkeypatch)
        monkeypatch.setattr(runner, "AUTO_READ_PAGES", pages)
        tr.relay.frame_sw, tr.relay.frame_sh = 1920, 1080
        shown = {"n": -1}

        async def fake_shot(task_id, *, force=False, attach=True):
            shown["n"] += 1
            tr._raw_frame = jpeg(20 * shown["n"])
            tr._frame_hash = str(shown["n"])
            if attach:
                tr._pending_image = tr._raw_frame
            return "ok: đã chụp"
        tr._screenshot = fake_shot

        async def fake_transcribe(task_id):
            return screens[min(shown["n"], len(screens) - 1)]
        tr._transcribe_page = fake_transcribe
        seen = scripted_llm(monkeypatch, [reply(calls=[tool_call("screenshot")]),
                                          reply("Tóm tắt từ mục 4"),
                                          reply("Tóm tắt đủ từ mục 1 tới mục 8")])
        await tr.run_task("t6", T6)
        return tr, seen
    return asyncio.run(go())


def test_early_answer_makes_the_agent_read_from_the_top(monkeypatch):
    tr, seen = run_t6(monkeypatch)
    sent = [c for c in tr.relay.calls]
    assert sent[:3] == [("key", {"name": "esc"}), ("hotkey", {"combo": "ctrl+home"}),
                        ("move", {"x": 960, "y": 540})]
    assert ("scroll", {"amount": -8}) in sent
    final_call = seen[-1]
    assert final_call["tools"] is None                      # no more scrolling
    text = "\n".join(str(m.get("content")) for m in final_call["messages"] if isinstance(m, dict))
    assert "TỰ đọc tài liệu từ ĐẦU tới CUỐI" in text
    assert "1. Tên dự án" in text and "8. Khó khăn" in text
    assert tr.relay.final()[-1]["result"] == "Tóm tắt đủ từ mục 1 tới mục 8"


def test_reading_stops_when_scrolling_brings_nothing_new(monkeypatch):
    tr, _ = run_t6(monkeypatch)
    scrolls = [c for c in tr.relay.calls if c[0] == "scroll"]
    assert len(scrolls) == 4            # pages 2-5; pages 4 and 5 add nothing -> stop


def test_page_limit_is_respected(monkeypatch):
    endless = ["Mở đầu dự án", "Khách hàng vending", "Tiến độ triển khai sáu tháng",
               "Ngân sách và chi phí linh kiện", "Rủi ro về nhiễu nguồn điện"] * 10
    tr, _ = run_t6(monkeypatch, pages=3, screens=endless)
    assert len([c for c in tr.relay.calls if c[0] == "scroll"]) == 2


def test_document_already_read_to_the_end_is_not_read_again(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        monkeypatch.setattr(runner, "AUTO_READ_PAGES", 15)
        tr.relay.frame_sw, tr.relay.frame_sh = 1920, 1080
        pages = iter(["Trang 1", "Trang 2", "Trang 2", "Trang 2"])

        async def fake_shot(task_id, *, force=False, attach=True):
            tr._raw_frame = jpeg(10)
            return "ok"
        tr._screenshot = fake_shot

        async def fake_transcribe(task_id):
            return next(pages)
        tr._transcribe_page = fake_transcribe
        scripted_llm(monkeypatch, [reply(calls=[tool_call("screenshot")]),
                                   reply(calls=[tool_call("scroll", amount=-8)]),
                                   reply(calls=[tool_call("screenshot")]),
                                   reply(calls=[tool_call("scroll", amount=-8)]),
                                   reply(calls=[tool_call("screenshot")]),
                                   reply("Tóm tắt")])
        await tr.run_task("t", T6)
        assert ("hotkey", {"combo": "ctrl+home"}) not in tr.relay.calls
    asyncio.run(go())


def test_document_pages_are_not_labelled_as_chat(monkeypatch):
    import llm

    async def go():
        tr = runner.TaskRunner(make_runner(monkeypatch).relay)
        tr._raw_frame = b"page"
        prompts = []

        async def fake_chat(**kwargs):
            prompts.append(kwargs["messages"][0]["content"])
            return reply("1. Tên dự án")
        monkeypatch.setattr(llm, "chat", fake_chat)
        tr._task_groups = ("word",)
        await tr._transcribe_page("t")
        tr._task_groups = ("zalo",)
        await tr._transcribe_page("t")
        assert "'Họ:'" not in prompts[0] and "'Họ:'" in prompts[1]
    asyncio.run(go())
