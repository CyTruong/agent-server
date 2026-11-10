"""The vision model transcribes each page it reads into the conversation."""
import asyncio

import llm
import runner
from test_loops import jpeg
from test_runner import make_runner, reply, scripted_llm, tool_call


def reading_run(monkeypatch, transcripts, max_chars=None):
    """Read len(transcripts) screens. transcripts[i] is what the model copies from
    screen i (None = the call fails)."""
    async def go():
        tr = make_runner(monkeypatch)
        if max_chars:
            monkeypatch.setattr(runner, "NOTES_MAX_CHARS", max_chars)
        current = {"page": -1}
        pages = iter(range(len(transcripts)))
        copied = []

        async def fake_shot(task_id, *, force=False, attach=True):
            current["page"] = next(pages)
            tr._raw_frame = jpeg(30 * current["page"])
            tr._frame_hash = str(current["page"])
            return "ok: đã chụp"
        tr._screenshot = fake_shot

        async def fake_transcribe(task_id):
            copied.append(current["page"])
            return transcripts[current["page"]]
        tr._transcribe_page = fake_transcribe

        async def fake_summary(notes):
            return "TÓM GỌN: " + notes[:40]
        tr._summarize_notes = fake_summary

        replies = [reply(calls=[tool_call("screenshot")])]
        for _ in range(len(transcripts) - 1):
            replies += [reply(calls=[tool_call("scroll", amount=-8)]),
                        reply(calls=[tool_call("screenshot")])]
        replies.append(reply("Tóm tắt xong"))
        seen = scripted_llm(monkeypatch, replies)
        await tr.run_task("t", "đọc file word hồ sơ và tóm tắt")
        text = "\n".join(str(m.get("content")) for m in seen[-1]["messages"] if isinstance(m, dict))
        return tr, seen, copied, text
    return asyncio.run(go())


PAGES = ["1. Tên dự án: Bo mạch máy bán hàng tự động",
         "2. Khách hàng: ngành bán lẻ\n3. Tiến độ: 6 tháng",
         "8. Khó khăn lớn nhất: nhiễu nguồn",
         "8. Khó khăn lớn nhất: nhiễu nguồn",
         "8. Khó khăn lớn nhất: nhiễu nguồn"]


def test_pages_are_noted_and_the_end_forces_an_answer(monkeypatch):
    tr, seen, copied, text = reading_run(monkeypatch, PAGES)
    assert copied == [0, 1, 2, 3, 4]
    assert "Ghi chú trang này (model chép lại)" in text and "3. Tiến độ: 6 tháng" in text
    assert text.count("nhiễu nguồn") == 1      # a repeated page is not noted twice
    assert seen[-1]["tools"] is None and tr.relay.final()[-1]["result"] == "Tóm tắt xong"


def test_a_failed_transcription_is_no_verdict(monkeypatch):
    tr, seen, _, _ = reading_run(monkeypatch, [PAGES[0], None, None, None, None])
    assert all(call["tools"] is not None for call in seen)   # never forced to stop
    assert tr.relay.final()[-1]["result"] == "Tóm tắt xong"


def test_long_notes_are_condensed(monkeypatch):
    _, _, _, text = reading_run(monkeypatch, PAGES, max_chars=60)
    assert "Ghi chú tổng hợp các trang đã đọc" in text
    assert "đã gộp vào 'Ghi chú tổng hợp'" in text
    assert text.count("Ghi chú tổng hợp các trang đã đọc") == 1   # older summary folded in


def test_transcribe_sends_the_clean_page_and_the_recent_notes(monkeypatch):
    async def go():
        tr = runner.TaskRunner(make_runner(monkeypatch).relay)
        tr._raw_frame = b"page-bytes"
        tr._notes_recent = ["2. Khách hàng: ngành bán lẻ"]
        calls = []

        async def fake_chat(**kwargs):
            calls.append(kwargs)
            return reply("3. Tiến độ")
        monkeypatch.setattr(llm, "chat", fake_chat)
        assert await tr._transcribe_page("t") == "3. Tiến độ"
        message = calls[0]["messages"][0]
        assert message["images"] == [b"page-bytes"] and "2. Khách hàng" in message["content"]
        assert "tools" not in calls[0]

        async def nothing_new(**kwargs):
            return reply("KHÔNG CÓ NỘI DUNG MỚI")
        monkeypatch.setattr(llm, "chat", nothing_new)
        assert await tr._transcribe_page("t") == ""

        async def broken(**kwargs):
            raise RuntimeError("ollama down")
        monkeypatch.setattr(llm, "chat", broken)
        assert await tr._transcribe_page("t") is None
    asyncio.run(go())
