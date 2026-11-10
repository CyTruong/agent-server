"""READ_NOTES: the vision model transcribes each page it reads."""
import asyncio

import runner
from test_loops import PAGES, jpeg
from test_runner import make_runner, ocr_box, reply, scripted_llm, tool_call


def reading_run(monkeypatch, mode, transcripts, ocr_pages=PAGES, max_chars=None):
    """Read len(ocr_pages) screens. transcripts[i] is what the model copies from
    screen i (None = the call fails)."""
    async def go():
        tr = make_runner(monkeypatch)
        monkeypatch.setattr(runner, "READ_NOTES", mode)
        if max_chars:
            monkeypatch.setattr(runner, "NOTES_MAX_CHARS", max_chars)
        current = {"page": -1}
        pages = iter(range(len(ocr_pages)))
        copied = []

        async def fake_shot(task_id, *, force=False, attach=True):
            current["page"] = next(pages)
            tr._raw_frame = jpeg(30 * current["page"])
            tr._frame_hash = str(current["page"])
            return "ok: đã chụp"
        tr._screenshot = fake_shot

        async def fake_ocr(task_id):
            if mode == "model":
                return None
            return [ocr_box(t, 100, 20 + 40 * i) for i, t in enumerate(ocr_pages[current["page"]])]
        tr._ocr_items = fake_ocr

        async def fake_transcribe(task_id):
            copied.append(current["page"])
            return transcripts[current["page"]]
        tr._transcribe_page = fake_transcribe

        async def fake_summary(notes):
            return "TÓM GỌN: " + notes[:40]
        tr._summarize_notes = fake_summary

        replies = [reply(calls=[tool_call("screenshot")])]
        for _ in range(len(ocr_pages) - 1):
            replies += [reply(calls=[tool_call("scroll", amount=-8)]),
                        reply(calls=[tool_call("screenshot")])]
        replies.append(reply("Tóm tắt xong"))
        seen = scripted_llm(monkeypatch, replies)
        await tr.run_task("t", "đọc file word hồ sơ và tóm tắt")
        text = "\n".join(str(m.get("content")) for m in seen[-1]["messages"] if isinstance(m, dict))
        return tr, seen, copied, text
    return asyncio.run(go())


MODEL_TEXT = ["1. Tên dự án: Bo mạch máy bán hàng tự động",
              "2. Khách hàng: ngành bán lẻ\n3. Tiến độ: 6 tháng",
              "8. Khó khăn lớn nhất: nhiễu nguồn",
              "", ""]


def test_both_mode_keeps_the_model_transcript_not_ocr(monkeypatch):
    tr, seen, copied, text = reading_run(monkeypatch, "both", MODEL_TEXT)
    assert "Ghi chú trang này (model chép lại)" in text
    assert "3. Tiến độ: 6 tháng" in text and "nhiễu nguồn" in text
    assert "Chữ MỚI trên trang này" not in text
    assert copied == [0, 1, 2]              # pages OCR saw as nothing new are not copied
    assert seen[-1]["tools"] is None and tr.relay.final()[-1]["result"] == "Tóm tắt xong"


def test_model_mode_decides_the_end_from_its_own_text(monkeypatch):
    transcripts = MODEL_TEXT[:3] + [MODEL_TEXT[2], MODEL_TEXT[2]]   # same page again
    tr, seen, copied, text = reading_run(monkeypatch, "model", transcripts)
    assert copied == [0, 1, 2, 3, 4]
    assert text.count("nhiễu nguồn") == 1    # repeated page not noted twice
    assert seen[-1]["tools"] is None


def test_a_failed_transcription_is_no_verdict(monkeypatch):
    transcripts = [MODEL_TEXT[0], None, None, None, None]
    tr, seen, _, _ = reading_run(monkeypatch, "model", transcripts)
    assert all(call["tools"] is not None for call in seen)   # never forced to stop
    assert tr.relay.final()[-1]["result"] == "Tóm tắt xong"


def test_long_notes_are_condensed(monkeypatch):
    tr, seen, _, text = reading_run(monkeypatch, "both", MODEL_TEXT, max_chars=60)
    assert "Ghi chú tổng hợp các trang đã đọc" in text
    assert "đã gộp vào 'Ghi chú tổng hợp'" in text
    assert text.count("Ghi chú tổng hợp các trang đã đọc") == 1   # older summary folded in


def test_ocr_mode_unchanged(monkeypatch):
    _, _, copied, text = reading_run(monkeypatch, "ocr", MODEL_TEXT)
    assert copied == [] and "Chữ MỚI trên trang này" in text


def test_transcribe_sends_the_clean_page_and_the_recent_notes(monkeypatch):
    import llm

    async def go():
        tr = make_runner(monkeypatch)
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
