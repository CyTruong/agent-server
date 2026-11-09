"""TaskRunner: the LLM <-> tool loop for one user request at a time."""
import asyncio
import hashlib
import time
import traceback

import config  # noqa: F401 - must load .env before web_search reads os.environ
import agent_log
import llm
import web_search
from agent_log import clip, dump_messages, log
from config import (
    CACHE_DB,
    CACHE_ENABLED,
    CACHE_MAX_AGE_DAYS,
    CACHE_THRESHOLD,
    DEBUG,
    DEVICE_TIMEOUT,
    KEEP_IMAGES,
    LLM_TIMEOUT,
    MAX_RETRIES,
    MAX_STEPS,
    MODEL,
    NT,
    NUM_CTX,
    PLAN_ENABLE,
    SCREEN_CACHE_TTL,
    SCREEN_MUTATING_TOOLS,
    SEARCH_ENABLE,
    TASK_TIMEOUT,
    THINK,
    UI_LOGS,
)
from grounding import GroundingMixin
from imaging import annotate_grid, prune_images
from intents import _is_ask_or_blocked, _needs_delivery, _needs_plan, parse_yes_no
from relay_client import RelayClient, new_request_id
from skill_select import build_system_prompt
from task_cache import TaskCache
from tools import TOOLS, TOOL_NAMES, is_dangerous, split_for_type


class TaskRunner(GroundingMixin):
    """Owns one active task at a time (a home single-user setup)."""

    def __init__(self, relay: RelayClient):
        self.relay = relay
        self.task_cache = TaskCache(
            CACHE_DB,
            enabled=CACHE_ENABLED,
            threshold=CACHE_THRESHOLD,
            max_age_days=CACHE_MAX_AGE_DAYS,
        )
        self._confirm_waiters: dict[str, asyncio.Future] = {}
        self._active: asyncio.Task | None = None
        self._pending_image: bytes | None = None
        self._raw_frame: bytes | None = None   # last screenshot, no grid (for zoom)
        self._frame_hash = ""
        self._frame_at = 0.0
        self._ocr_cache_hash = ""
        self._ocr_cache_at = 0.0
        self._ocr_cache_items: list[dict] | None = None
        self._executed_steps: list[dict] = []
        self._task_text = ""                     # current request (for refine prompt)
        # Mapping from the model's image-space coords to real screen pixels,
        # learned from the last screenshot (image size vs reported screen size).
        self._img_w = self._img_h = 0
        self._scr_w = self._scr_h = 0
        self._tars_ok = True   # cleared if the UI-TARS model is missing this session
        relay.on_chat = self.on_chat
        relay.on_cancel = self.on_cancel
        relay.on_confirm = self.on_confirm
        if UI_LOGS:
            self._log_queue = agent_log.attach_queue()
            self._drain_task = asyncio.create_task(self._drain_logs())

    async def _drain_logs(self):
        """Forward buffered log lines to the browser as relay 'log' messages.
        Best-effort: dropped silently when the relay is down."""
        while True:
            line = await self._log_queue.get()
            try:
                await self.relay.send_ui({"type": "log", "task_id": agent_log.current_task(),
                                          "text": line})
            except Exception:  # noqa: BLE001
                pass

    async def on_chat(self, msg: dict):
        text = msg.get("text", "").strip()
        task_id = msg.get("task_id") or f"task-{int(time.time())}"
        if not text:
            return
        if self._active and not self._active.done():
            await self.relay.send_ui({"type": "error", "task_id": task_id,
                                      "error": "một tác vụ khác đang chạy"})
            return
        self._active = asyncio.create_task(self.run_task(task_id, text))

    async def on_cancel(self, msg: dict):
        if self._active and not self._active.done():
            self._active.cancel()   # stops LLM waits, confirms and pending clicks now
        # Tell the device to flush its queue too, not just stop the agent.
        try:
            await self.relay.send_ui({"type": "cancel", "task_id": msg.get("task_id", "")})
        except Exception:  # noqa: BLE001
            pass
        await self.relay.send_ui({"type": "cancel_ack", "task_id": msg.get("task_id", "")})

    async def on_confirm(self, msg: dict):
        """UI answered a confirm_request; resolve the waiting _ask_confirm."""
        fut = self._confirm_waiters.get(msg.get("confirm_id", ""))
        if fut and not fut.done():
            fut.set_result(bool(msg.get("ok")))

    def _screen_cache_fresh(self) -> bool:
        """Return whether the current frame is safe to reuse briefly."""
        return bool(
            self._raw_frame
            and self._frame_hash
            and time.monotonic() - self._frame_at <= SCREEN_CACHE_TTL
        )

    def _invalidate_screen_cache(self) -> None:
        """Drop volatile screen/OCR state after a UI-changing action."""
        self._raw_frame = None
        self._pending_image = None
        self._frame_hash = ""
        self._frame_at = 0.0
        self._ocr_cache_hash = ""
        self._ocr_cache_at = 0.0
        self._ocr_cache_items = None
        self._img_w = self._img_h = 0
        self._scr_w = self._scr_h = 0

    def _store_frame(self, image: bytes) -> tuple[bytes, tuple[int, int]]:
        """Store a clean frame and prepare the local coordinate metadata."""
        self._raw_frame = image
        self._frame_hash = hashlib.sha256(image).hexdigest()
        self._frame_at = time.monotonic()
        self._ocr_cache_hash = ""
        self._ocr_cache_at = 0.0
        self._ocr_cache_items = None
        annotated, (width, height) = annotate_grid(image)
        self._img_w, self._img_h = width, height
        self._scr_w, self._scr_h = self.relay.frame_sw, self.relay.frame_sh
        return annotated, (width, height)

    async def _progress(self, task_id: str, step: int, message: str):
        await self.relay.send_ui({"type": "progress", "task_id": task_id,
                                  "step": step, "max_steps": MAX_STEPS, "message": message})

    def _record_verified_cache(self, text: str, skill_names: list[str]) -> None:
        """Persist only workflows that included a successful screenshot."""
        if not self._executed_steps:
            return
        has_screenshot = any(
            step["tool"] == "screenshot"
            and not step["result"].startswith("error")
            for step in self._executed_steps
        )
        has_error = any(
            step["result"].startswith("error") for step in self._executed_steps
        )
        if not has_screenshot or has_error:
            return
        try:
            self.task_cache.record_verified(text, skill_names, self._executed_steps)
            log("workflow cache: saved verified task")
        except Exception as exc:  # noqa: BLE001
            log(f"workflow cache: save failed: {exc}")

    async def run_task(self, task_id: str, text: str):
        """Run one request; whatever happens the UI gets a final event."""
        try:
            await asyncio.wait_for(self._execute(task_id, text), TASK_TIMEOUT)
        except asyncio.TimeoutError:
            log(f"[{task_id}] task timeout after {TASK_TIMEOUT}s")
            await self.relay.send_ui({"type": "done", "task_id": task_id,
                                      "result": "hết thời gian"})
        except asyncio.CancelledError:
            log(f"[{task_id}] cancelled")
            await self.relay.send_ui({"type": "done", "task_id": task_id,
                                      "result": "đã huỷ"})
        except Exception as e:  # noqa: BLE001 - never die without telling the UI
            log(f"[{task_id}] task crashed:\n{traceback.format_exc()}")
            await self.relay.send_ui({"type": "error", "task_id": task_id,
                                      "error": f"lỗi nội bộ: {e}"})

    async def _execute(self, task_id: str, text: str):
        agent_log.set_task(task_id)
        sys_prompt, skill_names = build_system_prompt(text)
        self._task_text = text
        self._executed_steps = []
        messages = [{"role": "system", "content": sys_prompt},
                    {"role": "user", "content": text + NT}]
        cache_match = self.task_cache.retrieve(text, skill_names)
        if cache_match:
            messages.append({
                "role": "user",
                "content": self.task_cache.prompt_hint(cache_match) + NT,
            })
            log(
                f"[{task_id}] workflow cache hit: "
                f"score={cache_match['score']} id={cache_match['id']}"
            )
        await self._progress(task_id, 0, "đã nhận yêu cầu")
        log(f"=== TASK {task_id} === {text!r}")
        log(f"[{task_id}] skills: {skill_names} (system prompt {len(sys_prompt)} chars)")
        empties = 0        # consecutive fully-empty LLM replies
        delivered = False  # has the agent actually typed/sent anything yet?
        deliver_nudges = 0 # times we pushed it to finish the "send" part
        needs_deliver = _needs_delivery(text) and await self._requires_delivery(task_id, text)

        # Plan-and-execute: decompose a multi-step request first, keep it as a
        # checklist in context so the model works through every step.
        if PLAN_ENABLE and _needs_plan(text):
            await self._progress(task_id, 0, "đang lập kế hoạch")
            plan = await self._make_plan(task_id, text)
            if plan:
                log(f"[{task_id}] kế hoạch:\n{plan}")
                messages.append({"role": "user", "content":
                    "Đây là kế hoạch các bước cho yêu cầu trên. Làm LẦN LƯỢT từng bước, "
                    "KHÔNG bỏ bước, chỉ kết thúc khi đã xong bước CUỐI. Bắt đầu bước 1 "
                    "ngay bằng cách gọi công cụ:\n" + plan + NT})

        for step in range(1, MAX_STEPS + 1):
            prune_images(messages, KEEP_IMAGES)
            try:
                has_img = any("images" in mm for mm in messages if isinstance(mm, dict))
                log(f"[{task_id}] step {step}: calling LLM ({MODEL}, {len(messages)} msgs"
                    f"{', +image' if has_img else ''}, think={THINK}) ...")
                dump_messages(task_id, messages)
                t0 = time.monotonic()
                resp = await llm.chat(model=MODEL, messages=messages,
                                      tools=TOOLS, think=THINK,
                                      options={"num_ctx": NUM_CTX})
                dt = time.monotonic() - t0
            except asyncio.TimeoutError:
                log(f"[{task_id}] LLM timeout sau {LLM_TIMEOUT}s (Ollama kẹt/đang tải model?)")
                await self.relay.send_ui({"type": "error", "task_id": task_id,
                                          "error": f"LLM quá {LLM_TIMEOUT}s không phản hồi "
                                                   "(kiểm tra Ollama: 'ollama ps', VRAM, num_ctx)"})
                return
            except Exception as e:  # noqa: BLE001
                log(f"[{task_id}] LLM error: {e}")
                await self.relay.send_ui({"type": "error", "task_id": task_id,
                                          "error": f"LLM lỗi: {e}"})
                return

            m = resp["message"]
            messages.append(m)
            calls = m.get("tool_calls") or []
            content = (m.get("content") or "").strip()
            # `thinking` is a model attribute on the ollama Message, not always a
            # dict key, so .get() can miss it - read via getattr as a fallback.
            think = ((m.get("thinking") if isinstance(m, dict) else None)
                     or getattr(m, "thinking", "") or "").strip()
            log(f"[{task_id}] step {step}: LLM {dt:.1f}s -> {len(calls)} tool_call(s), "
                f"content={len(content)} chars, thinking={len(think)} chars")
            # Raw reply (images stripped) so a blank turn is diagnosable.
            if DEBUG:
                log(f"[{task_id}]   raw: {clip(str(m), 2000)}")
            if calls:
                log(f"[{task_id}]   tool_calls: "
                    + "; ".join(f"{c['function']['name']}({c['function'].get('arguments', {})})"
                                for c in calls))
            if think:
                log(f"[{task_id}]   thinking: {clip(think)}")
            if content:
                log(f"[{task_id}]   content: {clip(content)}")

            if not calls:
                if content or think:
                    empties = 0
                    # Multi-step guard: the request includes a SEND/POST action but
                    # nothing has been typed/sent yet (model gathered info then tried
                    # to stop). Push it to carry the result into the delivery step
                    # instead of ending.
                    if (needs_deliver and not delivered and deliver_nudges < 2
                            and not _is_ask_or_blocked(content)):
                        deliver_nudges += 1
                        log(f"[{task_id}] chưa gửi mà định dừng -> nhắc hoàn tất "
                            f"({deliver_nudges})")
                        messages.append({"role": "user", "content":
                            "CHƯA XONG. Yêu cầu còn phần GỬI/NHẮN/ĐĂNG nội dung mà bạn "
                            "chưa gõ hay gửi gì cả. Đừng kết thúc. Dùng NỘI DUNG bạn vừa "
                            "có ở trên, thực hiện tiếp bằng công cụ: mở đúng nơi (khung "
                            "chat/ô soạn), screenshot, tap vào ô nhập, type nội dung, rồi "
                            "key enter để gửi. Nếu chưa xác định được đúng người/nơi nhận "
                            "thì DỪNG và hỏi lại, không gửi đại." + NT})
                        continue
                    result = content or f"(suy nghĩ) {think}"
                    log(f"[{task_id}] DONE: {result[:160]}")
                    self._record_verified_cache(text, skill_names)
                    await self.relay.send_ui({"type": "done", "task_id": task_id,
                                              "result": result})
                    return
                # Completely empty reply: nudge once or twice before giving up,
                # instead of ending the task silently.
                empties += 1
                if empties >= 3:
                    log(f"[{task_id}] DONE: empty {empties}x, giving up")
                    await self.relay.send_ui({"type": "done", "task_id": task_id,
                                              "result": "(model không phản hồi sau nhiều lần nhắc)"})
                    return
                log(f"[{task_id}] empty reply ({empties}) -> nudging")
                messages.append({"role": "user", "content":
                    "Bạn chưa gọi công cụ và chưa trả lời. Nếu công việc chưa xong, "
                    "hãy gọi MỘT công cụ tiếp theo (move/click/type/key/scroll/screenshot). "
                    "Nếu đã xong, hãy trả lời ngắn gọn bằng văn bản." + NT})
                continue

            empties = 0
            for call in calls:
                name = call["function"]["name"]
                args = call["function"].get("arguments", {}) or {}
                log(f"[{task_id}] step {step}: tool -> {name} {args}")
                await self._progress(task_id, step, f"{name} {args}")
                try:
                    result = await self._run_tool(task_id, name, args)
                except Exception as e:  # noqa: BLE001 - e.g. malformed model arguments
                    log(f"[{task_id}] tool {name} crashed:\n{traceback.format_exc()}")
                    result = f"error: không chạy được {name}: {e}"
                log(f"[{task_id}] step {step}: {name} => {result[:120]}")
                self._executed_steps.append({
                    "tool": name,
                    "arguments": dict(args),
                    "result": result,
                })
                # Mark that the agent has started delivering (typed text) so the
                # multi-step guard doesn't keep nudging once the send is under way.
                if name == "type" and not result.startswith("error"):
                    delivered = True
                messages.append({"role": "tool", "name": name, "content": result})
                # A screenshot attaches the captured image as the next turn's
                # input so the vision model can read the screen.
                if name == "screenshot" and self._pending_image is not None:
                    note = (f"Ảnh màn hình {self._img_w}x{self._img_h}, có LƯỚI TOẠ ĐỘ màu đỏ: "
                            "các vạch dọc/ngang cách nhau 100 px, có ghi số pixel. "
                            "Hãy ĐỌC toạ độ của phần tử theo lưới này (nội suy giữa hai vạch "
                            "gần nhất), đừng đoán. Cho move/click theo pixel trên ảnh; hệ "
                            "thống tự quy đổi ra màn hình thật.")
                    messages.append({"role": "user", "content": note + NT,
                                     "images": [self._pending_image]})
                    self._pending_image = None

        await self.relay.send_ui({"type": "done", "task_id": task_id,
                                  "result": f"đạt giới hạn {MAX_STEPS} bước"})

    async def _run_tool(self, task_id: str, name: str, args: dict) -> str:
        if name not in TOOL_NAMES:
            return f"error: tool '{name}' không được phép"
        if name == "screenshot":
            return await self._screenshot(task_id, force=True, attach=True)
        if name == "web_search":
            if not SEARCH_ENABLE:
                return "error: web_search đang tắt (đặt SEARCH_ENABLE=true)"
            q = str(args.get("query", "")).strip()
            k = int(args.get("max_results", 5) or 5)
            log(f"[{task_id}] web_search: {q!r} (k={k})")
            res = await asyncio.to_thread(web_search.search, q, k)
            log(f"[{task_id}] web_search -> {clip(res, 300)}")
            return res
        if name == "fetch_url":
            if not SEARCH_ENABLE:
                return "error: fetch_url đang tắt (đặt SEARCH_ENABLE=true)"
            url = str(args.get("url", "")).strip()
            log(f"[{task_id}] fetch_url: {url}")
            res = await asyncio.to_thread(web_search.fetch_page, url)
            log(f"[{task_id}] fetch_url -> {clip(res, 200)}")
            return res
        if name == "tap":
            return await self._tap(task_id, args)
        # Image-space coords from the model: refine by zoom, then map to screen.
        if name in ("move", "click") and "x" in args and "y" in args:
            ix, iy = int(args["x"]), int(args["y"])
            ix, iy = await self._refine_point(task_id, ix, iy)
            self._mark_click(task_id, ix, iy, f"{name}_click")
            args = dict(args)
            if self._img_w and self._scr_w:
                args["x"] = round(ix * self._scr_w / self._img_w)
                args["y"] = round(iy * self._scr_h / self._img_h)
                log(f"[{task_id}] {name} img({ix},{iy}) -> screen({args['x']},{args['y']})")
            else:
                args["x"], args["y"] = ix, iy
        if is_dangerous(name, args):
            ok = await self._ask_confirm(task_id, name, args)
            if not ok:
                return "error: người dùng từ chối hành động nguy hiểm"

        if name == "type" and isinstance(args.get("text"), str):
            return await self._type_text(task_id, args["text"])

        # Exactly one attempt: after a timeout the action may still have run, and
        # repeating it would type or send the message twice.
        try:
            res = await self.relay.call_device(name, args, task_id, timeout=DEVICE_TIMEOUT)
        except asyncio.TimeoutError:
            return ("error: thiết bị không phản hồi (timeout), KHÔNG rõ lệnh đã chạy chưa. "
                    "Hãy screenshot kiểm tra trước khi làm lại.")
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"
        if res.get("ok"):
            detail = (res.get("result") or {}).get("detail", "")
            if name in SCREEN_MUTATING_TOOLS:
                self._invalidate_screen_cache()
            return f"ok {detail}".strip()
        return f"error: {res.get('error', 'thất bại')}"

    async def _type_text(self, task_id: str, text: str) -> str:
        """Type text of any length as sequential chunks. Stops at the first
        failure and says so, because the model must not press Enter on half a
        message."""
        try:
            chunks = split_for_type(text)
        except ValueError as e:
            return f"error: không gõ được: {e}"
        for index, chunk in enumerate(chunks, start=1):
            failure = ""
            try:
                res = await self.relay.call_device("type", {"text": chunk}, task_id,
                                                   timeout=DEVICE_TIMEOUT)
                if not res.get("ok"):
                    failure = res.get("error", "thất bại")
            except asyncio.TimeoutError:
                failure = "thiết bị không phản hồi (timeout), KHÔNG rõ đoạn này đã gõ chưa"
            except Exception as e:  # noqa: BLE001
                failure = str(e)
            if failure:
                self._invalidate_screen_cache()
                return (f"error: lỗi khi gõ đoạn {index}/{len(chunks)}: {failure}. ĐỪNG nhấn Enter; "
                        "screenshot kiểm tra ô nhập, xoá phần đã gõ rồi gõ lại.")
        self._invalidate_screen_cache()
        return f"ok đã gõ {len(text)} ký tự"

    async def _requires_delivery(self, task_id: str, text: str) -> bool:
        """Ask the LLM whether a request with a delivery word really means sending
        something to someone. On any failure answer no: a missed nudge only ends
        the task early, a wrong one can send text into whatever chat is open."""
        prompt = (
            "Yêu cầu sau có đòi hỏi GỬI hoặc ĐĂNG một nội dung tới NGƯỜI KHÁC hay lên một "
            "ứng dụng trên máy tính không? Ví dụ: 'nhắn cho An là tôi đến muộn' = CO; "
            "'trả lời giúp tôi 2+2 bằng mấy' = KHONG; 'bình luận về bộ phim này đi' = KHONG. "
            "Chỉ trả về đúng một từ CO hoặc KHONG.\n\nYêu cầu: " + text + " /no_think")
        try:
            resp = await llm.chat(model=MODEL, think=False, options={"num_ctx": NUM_CTX},
                                  messages=[{"role": "user", "content": prompt}])
            reply = resp["message"].get("content") or ""
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] delivery check failed ({e}) -> không nhắc gửi")
            return False
        log(f"[{task_id}] cần gửi? {reply.strip()[:20]!r}")
        return parse_yes_no(reply)

    async def _make_plan(self, task_id: str, text: str):
        """One text-only LLM call: decompose the request into a short numbered
        step list to keep the agent on track for multi-step tasks. Returns the
        plan text, or None on any failure (task just runs without a plan)."""
        prompt = (
            "Phân rã yêu cầu sau thành các BƯỚC hành động ngắn gọn để điều khiển máy "
            "tính Windows (mỗi bước 1 dòng, đánh số, bắt đầu bằng động từ). Gộp thao "
            "tác nhỏ, chỉ nêu bước chính (vd: 'Tra thông tin X'; 'Mở khung chat với Y'; "
            "'Gõ nội dung'; 'Gửi'). KHÔNG giải thích, KHÔNG viết gì ngoài danh sách.\n\n"
            "Yêu cầu: " + text)
        try:
            resp = await llm.chat(model=MODEL, think=False,
                                  options={"num_ctx": NUM_CTX},
                                  messages=[{"role": "user", "content": prompt + " /no_think"}])
            plan = (resp["message"].get("content") or "").strip()
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] plan: lỗi ({e}) -> chạy không kế hoạch")
            return None
        return plan or None

    async def _screenshot(
        self,
        task_id: str,
        *,
        force: bool = False,
        attach: bool = True,
    ) -> str:
        """Get a screen frame, reusing the short-lived cache when allowed."""
        if not force and self._screen_cache_fresh():
            annotated, _ = annotate_grid(self._raw_frame)
            if attach:
                self._pending_image = annotated
            log(f"[{task_id}] screenshot: short-lived cache hit")
            return (
                f"ok: dùng screenshot cache ({self._img_w}x{self._img_h}, "
                "ảnh đính kèm lượt sau)"
            )

        self._invalidate_screen_cache()
        # Capturing has no side effects, so unlike input actions it may be retried.
        last_error = "error: không chụp được"
        for attempt in range(1, MAX_RETRIES + 2):
            request_id = new_request_id()
            waiter = self.relay.arm_frame(request_id)   # arm BEFORE the call: the frame
            try:                                        # arrives before device_result
                log(f"[{task_id}] screenshot: enqueue aishot ({attempt}), waiting device_result ...")
                res = await self.relay.call_device("screenshot", {}, task_id,
                                                   timeout=DEVICE_TIMEOUT,
                                                   request_id=request_id)
                break
            except asyncio.TimeoutError:
                log(f"[{task_id}] screenshot: device_result TIMEOUT (PC agent chạy chưa?)")
                last_error = "error: thiết bị không phản hồi (timeout)"
            except Exception as e:  # noqa: BLE001
                log(f"[{task_id}] screenshot: device error {e}")
                last_error = f"error: {e}"
        else:
            return last_error
        if not res.get("ok"):
            return f"error: {res.get('error', 'chụp thất bại')}"
        log(f"[{task_id}] screenshot: device ok, waiting JPEG frame ...")
        try:
            img = await asyncio.wait_for(waiter, 12)
        except asyncio.TimeoutError:
            img = None
        log(f"[{task_id}] screenshot: frame {'received ' + str(len(img)) + ' B' if img else 'NOT received'}")
        if img:
            annotated, (w, h) = self._store_frame(img)
            if attach:
                self._pending_image = annotated
            self._save(task_id, "shot", img, "jpg")
            self._save(task_id, "shot_grid", annotated)
            log(f"[{task_id}] screenshot: image {self._img_w}x{self._img_h} (grid), "
                f"screen {self._scr_w}x{self._scr_h}")
            return (f"ok: đã chụp màn hình ({self._img_w}x{self._img_h}, có lưới toạ độ, "
                    "ảnh đính kèm lượt sau)")
        return "ok: đã gửi lệnh chụp nhưng chưa nhận được ảnh (PC agent đang chạy?)"

    async def _ask_confirm(self, task_id: str, name: str, args: dict,
                           message: str = "") -> bool:
        """Block until the UI answers a confirm_request, or time out (deny)."""
        cid = f"{task_id}:{name}:{time.time()}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._confirm_waiters[cid] = fut
        await self.relay.send_ui({"type": "confirm_request", "task_id": task_id,
                                  "confirm_id": cid, "tool": name, "arguments": args,
                                  "message": message or f"Xác nhận hành động: {name} {args}"})
        # on_confirm() resolves this future when the UI answers. No answer in 60s
        # -> deny (fail safe).
        try:
            return await asyncio.wait_for(fut, 60)
        except asyncio.TimeoutError:
            return False
        finally:
            self._confirm_waiters.pop(cid, None)
