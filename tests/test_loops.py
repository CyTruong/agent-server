"""Two loops seen on real runs: reopening a file that is already open, and
scrolling on after the whole document has been read."""
import asyncio
import io

from PIL import Image

import runner
from progress import ReadingTracker
from test_region import region_runner
from test_runner import make_runner, reply, scripted_llm, tool_call

FILE = "Ho-so-du-an-bo-mach-may-ban-hang-tu-dong"
REQUEST = f"Mở tài liệu {FILE} và tóm tắt cho tôi"


# --- 1. do not open an open file again -------------------------------------------

def open_runner(monkeypatch, title):
    tr = region_runner(monkeypatch, text=title)
    tr._task_text = REQUEST
    tr._task_groups = ("word",)
    return tr


def test_search_is_refused_when_the_title_shows_the_file(monkeypatch):
    async def go():
        tr = open_runner(monkeypatch, f"{FILE}.docx - Word")
        result = await tr._run_tool("t", "hotkey", {"combo": "win+s"})
        assert result.startswith("error: KHÔNG mở lại")
        assert [c[0] for c in tr.relay.calls] == ["screenshot"]     # title capture only
    asyncio.run(go())


def test_ocr_noise_in_the_title_still_matches(monkeypatch):
    async def go():
        tr = open_runner(monkeypatch, "Ho-so-du-an-bo-mach-may-ban-hang-tu-dong docx  Word")
        assert (await tr._run_tool("t", "hotkey", {"combo": "win+s"})).startswith("error")
    asyncio.run(go())


def test_search_goes_ahead_for_another_window(monkeypatch):
    async def go():
        tr = open_runner(monkeypatch, "Zalo - A Vinh")
        result = await tr._run_tool("t", "hotkey", {"combo": "win+s"})
        assert result.startswith("ok") and tr.relay.calls[-1][0] == "hotkey"
    asyncio.run(go())


def test_follow_up_without_a_file_name_uses_the_previous_request(monkeypatch):
    async def go():
        tr = open_runner(monkeypatch, f"{FILE}.docx - Word")
        tr._task_text = "sửa tiêu đề cho tôi"
        tr._history = [{"at": runner.time.monotonic(), "request": REQUEST, "result": "",
                        "groups": ("word",)}]
        assert (await tr._run_tool("t", "hotkey", {"combo": "win+s"})).startswith("error")
    asyncio.run(go())


def test_other_apps_are_not_checked(monkeypatch):
    async def go():
        tr = open_runner(monkeypatch, f"{FILE}.docx - Word")
        tr._task_groups = ("zalo",)
        assert (await tr._run_tool("t", "hotkey", {"combo": "win+s"})).startswith("ok")
    asyncio.run(go())


# --- 2. stop when reading brings nothing new ---------------------------------------

def test_tracker_reports_only_new_lines():
    reader = ReadingTracker()
    fresh, _ = reader.page(["1. Tên dự án", "Bo mạch điều khiển"])
    assert fresh == ["1. Tên dự án", "Bo mạch điều khiển"]
    reader.action("scroll")
    fresh, nothing_new = reader.page(["Bo mạch điều khiên", "2. Khách hàng"])   # OCR noise
    assert fresh == ["2. Khách hàng"] and not nothing_new
    reader.action("scroll")
    _, nothing_new = reader.page(["2. Khách hàng"])
    assert nothing_new and reader.stale == 1


def test_no_move_no_verdict():
    reader = ReadingTracker()
    reader.page(["a line of text"])
    _, nothing_new = reader.page(["a line of text"])     # second look, no scroll between
    assert not nothing_new and reader.stale == 0


def jpeg(shade):
    out = io.BytesIO()
    Image.new("RGB", (64, 36), (shade, shade, shade)).save(out, format="JPEG")
    return out.getvalue()


PAGES = [["1. Tên dự án", "Bo mạch máy bán hàng"],
         ["2. Khách hàng", "3. Tiến độ"],
         ["8. Khó khăn lớn nhất"],
         ["8. Khó khăn lớn nhất"],                 # grey space past the end
         ["8. Khó khăn lớn nhất"]]


def test_reading_to_the_end_forces_an_answer(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        pages = iter(range(len(PAGES)))
        current = {}

        async def fake_shot(task_id, *, force=False, attach=True):
            current["page"] = next(pages)
            tr._raw_frame = jpeg(40 * current["page"])        # the screen keeps changing
            tr._frame_hash = str(current["page"])
            return "ok: đã chụp"
        tr._screenshot = fake_shot

        async def fake_transcribe(task_id):
            return "\n".join(PAGES[current["page"]])
        tr._transcribe_page = fake_transcribe
        replies = [reply(calls=[tool_call("screenshot")])]
        for _ in range(len(PAGES) - 1):
            replies += [reply(calls=[tool_call("scroll", amount=-8)]),
                        reply(calls=[tool_call("screenshot")])]
        replies.append(reply("Tóm tắt: 1. Tên dự án ... 8. Khó khăn"))
        seen = scripted_llm(monkeypatch, replies)
        await tr.run_task("t", "đọc file word hồ sơ và tóm tắt")
        last = seen[-1]
        assert last["tools"] is None                      # answer forced, no more scrolling
        text = "\n".join(str(m.get("content")) for m in last["messages"] if isinstance(m, dict))
        assert "1. Tên dự án" in text and "2. Khách hàng" in text   # kept after image pruning
        assert "Đã đọc HẾT tài liệu" in text
        assert tr.relay.final()[-1]["result"].startswith("Tóm tắt")
        assert len(seen) == 10                            # stopped at the 2nd page without news
    asyncio.run(go())


def test_reading_note_is_skipped_for_non_reading_tasks(monkeypatch):
    async def go():
        tr = make_runner(monkeypatch)
        called = []

        async def fake_transcribe(task_id):
            called.append(1)
            return ""
        tr._transcribe_page = fake_transcribe

        async def fake_shot(task_id, *, force=False, attach=True):
            tr._raw_frame = jpeg(10)
            return "ok"
        tr._screenshot = fake_shot
        scripted_llm(monkeypatch, [reply(calls=[tool_call("screenshot")]), reply("xong")])
        await tr.run_task("t", "mở notepad")
        assert called == []
    asyncio.run(go())


def test_a_stray_line_on_a_full_page_is_still_nothing_new():
    reader = ReadingTracker()
    page = [f"dòng nội dung số {i}" for i in range(12)]
    reader.page(page)
    reader.action("scroll")
    _, nothing_new = reader.page(page + ["Trang 5 / 5"])
    assert nothing_new
