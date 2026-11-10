"""Native-resolution region captures: zoom tool, tap fallback, region maths."""
import asyncio
import io

from PIL import Image

import grounding
import runner
from regions import ZOOM_AREAS, fraction_box, image_box_to_screen
from test_runner import reply, scripted_llm, tool_call
from tools import TOOL_NAMES, is_dangerous


def jpeg(w, h):
    out = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(out, format="JPEG")
    return out.getvalue()


class RegionRelay:
    """A relay whose PC either supports region capture or (old) ignores it."""
    frame_sw, frame_sh = 1920, 1080

    def __init__(self, supports_region=True):
        self.supports_region = supports_region
        self.calls, self.ui = [], []
        self._waiter = None

    async def send_ui(self, msg):
        self.ui.append(msg)

    def arm_frame(self, request_id=""):
        self._waiter = asyncio.get_running_loop().create_future()
        return self._waiter

    async def call_device(self, tool, args, task_id, timeout=10.0, request_id=""):
        self.calls.append((tool, args))
        if tool != "screenshot":
            return {"ok": True, "result": {"detail": ""}}
        region = args.get("region")
        if region and self.supports_region:
            x, y, w, h = region
            self._waiter.set_result(jpeg(w, h))
            detail = f"aishot: region {x},{y} {w}x{h} -> {w}x{h} JPEG 1 byte"
        else:
            self._waiter.set_result(jpeg(1280, 720))
            detail = "aishot: 1280x720 JPEG 1 byte"
        return {"ok": True, "result": {"detail": detail}}

    def final(self):
        return [m for m in self.ui if m["type"] in ("done", "error")]


def region_runner(monkeypatch, supports_region=True, text=""):
    """A runner on RegionRelay whose vision model reads `text` from any image."""
    monkeypatch.setattr(runner, "CACHE_ENABLED", False)
    monkeypatch.setattr(runner, "PLAN_ENABLE", False)
    monkeypatch.setattr(grounding, "SAVE_IMAGES", False)
    monkeypatch.setattr(runner, "FIRST_LOOK", False)   # tests that want it turn it on
    relay = RegionRelay(supports_region)
    tr = runner.TaskRunner(relay)
    tr._shot_size = (1280, 720)
    tr.read_calls = []

    async def read(task_id, image, purpose=""):
        tr.read_calls.append(purpose)
        return text
    tr._read_image = read
    return tr


def test_region_maths():
    assert fraction_box(ZOOM_AREAS["title"], 1920, 1080) == (0, 0, 1920, 54)
    assert image_box_to_screen([100, 50, 300, 150], 1280, 720, 1920, 1080) == (150, 75, 300, 150)
    assert image_box_to_screen([300, 150, 100, 50], 1280, 720, 1920, 1080) == (150, 75, 300, 150)
    assert image_box_to_screen([0, 0, 2, 2], 1280, 720, 1920, 1080)[2:] == (16, 16)


def test_zoom_tool_is_registered_and_safe():
    assert "zoom" in TOOL_NAMES and not is_dangerous("zoom", {"area": "title"})


def test_zoom_title_asks_for_the_title_bar_at_native_resolution(monkeypatch):
    async def go():
        tr = region_runner(monkeypatch, text="Ho-so.docx - Word")
        result = await tr._run_tool("t", "zoom", {"area": "title"})
        assert tr.relay.calls == [("screenshot", {"region": [0, 0, 1920, 54]})]
        assert "độ phân giải thật" in result and "Ho-so.docx - Word" in result
        assert Image.open(io.BytesIO(tr._pending_image)).size == (1920, 54)
        assert "PHÓNG TO" in tr._pending_note
    asyncio.run(go())


def test_zoom_box_uses_the_last_screenshot_grid(monkeypatch):
    async def go():
        tr = region_runner(monkeypatch)
        await tr._run_tool("t", "zoom", {"box": [100, 50, 300, 150]})
        assert tr.relay.calls == [("screenshot", {"region": [150, 75, 300, 150]})]
    asyncio.run(go())


def test_old_device_falls_back_to_cropping(monkeypatch):
    async def go():
        tr = region_runner(monkeypatch, supports_region=False)
        result = await tr._run_tool("t", "zoom", {"area": "title"})
        assert "chưa hỗ trợ chụp vùng" in result
        assert Image.open(io.BytesIO(tr._pending_image)).size == (1280, 36)
    asyncio.run(go())


def test_bad_zoom_arguments(monkeypatch):
    async def go():
        tr = region_runner(monkeypatch)
        assert (await tr._run_tool("t", "zoom", {"area": "nowhere"})).startswith("error")
        tr._shot_size = (0, 0)
        assert (await tr._run_tool("t", "zoom", {"box": [1, 2, 3, 4]})).startswith("error")
    asyncio.run(go())


def test_zoom_image_reaches_the_model_with_its_own_caption(monkeypatch):
    async def go():
        tr = region_runner(monkeypatch)
        seen = scripted_llm(monkeypatch, [reply(calls=[tool_call("zoom", area="title")]),
                                          reply("xong")])
        await tr.run_task("t", "file nào đang mở")
        notes = [m["content"] for m in seen[-1]["messages"]
                 if isinstance(m, dict) and m.get("images")]
        assert len(notes) == 1 and notes[0].startswith("Ảnh PHÓNG TO vùng title")
    asyncio.run(go())


def test_zoom_counts_as_looking_at_search_results(monkeypatch):
    async def go():
        tr = region_runner(monkeypatch)
        tr._search_open = True
        await tr._run_tool("t", "zoom", {"area": "left"})
        assert tr._search_open is False
    asyncio.run(go())
