"""TaskRunner: the LLM <-> tool loop for one user request at a time."""
import asyncio
import contextlib
import hashlib
import json
import re
import time
import traceback

import config  # noqa: F401 - must load .env before web_search reads os.environ
import agent_log
import llm
import ocr
import web_search
from agent_log import clip, dump_messages, log
from config import (
    CACHE_DB,
    CACHE_ENABLED,
    CACHE_MAX_AGE_DAYS,
    CACHE_THRESHOLD,
    CHOICE_TIMEOUT,
    CONTEXT_RESULT_CHARS,
    CONTEXT_TTL,
    CONTEXT_TURNS,
    FIRST_LOOK,
    DEBUG,
    DEVICE_TIMEOUT,
    KEEP_IMAGES,
    LLM_TIMEOUT,
    MAX_RETRIES,
    MAX_STEPS,
    MODEL,
    NOTES_MAX_CHARS,
    NT,
    NUM_CTX,
    OCR_ENABLE,
    OCR_LANG,
    PLAN_ENABLE,
    READ_NOTES,
    SCREEN_CACHE_TTL,
    SCREEN_MUTATING_TOOLS,
    SEARCH_ENABLE,
    STUCK_LIMIT,
    TASK_TIMEOUT,
    THINK,
)
from grounding import GroundingMixin
from imaging import annotate_grid, crop_image, image_size, prune_images, upscale
from progress import ProgressWatch, ReadingTracker
from regions import ZOOM_AREAS, fraction_box, image_box_to_screen, ocr_lines
from intents import (
    _is_ask_or_blocked,
    _needs_delivery,
    _needs_plan,
    parse_choice,
    parse_yes_no,
)
from relay_client import RelayClient, new_request_id
from matching import _sim, requested_persons
from skill_select import build_system_prompt, gated_groups
from task_cache import TaskCache, is_verified_workflow
from tools import TOOLS, TOOL_NAMES, is_dangerous, split_for_type


# Words that refer back to an earlier request ("gửi tóm tắt TRÊN cho anh Vinh").
REFERENCE_WORDS = ("trên", "đó", "này", "vừa", "vừa rồi", "lúc nãy", "ấy", "kết quả",
                   "tiếp", "nữa")
# Window title words showing each app is in front (compared without accents/spaces).
APP_TITLE_HINTS = {"zalo": ("zalo",), "facebook": ("facebook", "messenger"),
                   "word": ("word",)}
APP_LABELS = {"zalo": "Zalo", "facebook": "Facebook", "word": "Microsoft Word"}

def _same_name(query: str, shown: str) -> bool:
    """Is the search result `shown` exactly what was typed? Extension, accents and
    separators aside; a name cut with "..." must start like the query. Close is
    not enough: "Ho-so" must not open "Ho-so-cu.docx"."""
    cut = shown.rstrip().endswith(("...", "…"))
    shown = re.sub(r"(\.{3}|…)\s*$", "", shown.strip())
    shown = re.sub(r"\.(docx?|xlsx?|pptx?|pdf|txt|exe|lnk)$", "", shown, flags=re.IGNORECASE)
    query = re.sub(r"\.(docx?|xlsx?|pptx?|pdf|txt|exe|lnk)$", "", query.strip(), flags=re.IGNORECASE)
    q, s = _file_key(query), _file_key(shown)
    if not q or not s:
        return False
    return q == s or (cut and len(s) >= 12 and q.startswith(s))


# The same failing call is refused after this many identical failures.
REPEAT_FAIL_LIMIT = 2
NEVER_BLOCKED = ("screenshot", "zoom", "ask_user", "web_search", "fetch_url")
# Errors that are instructions from the agent, not failures of the action.
GUIDANCE_ERRORS = ("CHƯA nhấn Enter", "KHÔNG mở lại", "KHÔNG chạy")
NOTHING_NEW = "KHÔNG CÓ NỘI DUNG MỚI"
SEARCH_TOP_LABELS = ("Best match", "Kết quả phù hợp nhất", "Phù hợp nhất")

MODEL_NOTE_HEAD = "Ghi chú trang này (model chép lại)"
OCR_NOTE_HEAD = "Chữ MỚI trên trang này (OCR, có thể sai chính tả)"
SUMMARY_HEAD = "Ghi chú tổng hợp các trang đã đọc (đã tóm gọn):"

# Skills that read page by page; their screenshots get the page text attached.
READING_SKILLS = ("word/read-file", "word/find-related", "zalo/read-chat",
                  "facebook/browse-feed")


def _file_key(text: str) -> str:
    """Lowercase letters/digits only, accents removed: compares file names with
    window titles regardless of "-", "_", spaces or OCR dropping accents."""
    import unicodedata
    text = unicodedata.normalize("NFKD", (text or "").replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


# Typed alone as a chat message, these clear the device's context.
RESET_COMMANDS = frozenset({"bắt đầu lại", "quên hết", "xoá ngữ cảnh", "xóa ngữ cảnh",
                            "reset", "/moi", "/new"})


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
        self._choice: asyncio.Future | None = None   # an ask_user waiting for a reply
        self._deadline = 0.0                          # task timeout, pushed back while
        self._user_wait_since: float | None = None    # the user is answering
        self._active: asyncio.Task | None = None
        self._pending_image: bytes | None = None
        self._raw_frame: bytes | None = None   # last screenshot, no grid (for zoom)
        self._frame_hash = ""
        self._frame_at = 0.0
        self._ocr_cache_hash = ""
        self._ocr_cache_at = 0.0
        self._ocr_cache_items: list[dict] | None = None
        self._executed_steps: list[dict] = []
        self._approved_names: set[str] = set()   # confirmed by the user in this task
        self._task_text = ""                     # current request (for refine prompt)
        # Mapping from the model's image-space coords to real screen pixels,
        # learned from the last screenshot (image size vs reported screen size).
        self._img_w = self._img_h = 0
        self._scr_w = self._scr_h = 0
        self._tars_ok = True   # cleared if the UI-TARS model is missing this session
        # One runner per device (see sessions.SessionManager); "" = single device.
        self.device_id = getattr(relay, "device_id", "")
        self._history: list[dict] = []        # recent finished requests (context)
        self._task_groups: tuple = ()          # app skill groups of the current task
        self._last_final: dict | None = None   # last done/error event sent
        self._search_open = False   # Windows Search opened, results not looked at yet
        self._search_query = ""     # what was typed into Windows Search
        self._fail_counts: dict = {}   # identical failed calls in this task
        self._pending_note = None   # caption for _pending_image when it is not a screenshot
        self._shot_size = (0, 0)    # size of the last full screenshot (kept for zoom boxes)
        self._notes_chars = 0       # characters of page notes kept in this task
        self._notes_recent: list[str] = []   # last noted lines, so a page is not copied twice
        self._note_dedupe = None    # drops lines the model already transcribed
        self._stuck_warned = False  # the task changed nothing on screen at some point

    async def _ui(self, msg: dict):
        """Send an event to this device's browser, remembering the final one."""
        if msg.get("type") in ("done", "error"):
            self._last_final = msg
        await self.relay.send_ui(msg)

    def _recent_history(self) -> list:
        """Recent requests to show the next one; empty when context is disabled
        (CONTEXT_TTL_SECONDS or CONTEXT_TURNS set to 0)."""
        if CONTEXT_TTL <= 0 or CONTEXT_TURNS <= 0:
            return []
        now = time.monotonic()
        self._history = [h for h in self._history if now - h["at"] <= CONTEXT_TTL]
        return self._history[-CONTEXT_TURNS:]

    @staticmethod
    def _history_note(history: list) -> str:
        now = time.monotonic()
        lines = ["Ngữ cảnh: các yêu cầu GẦN ĐÂY trên máy này (cũ → mới). Dùng để hiểu yêu cầu "
                 "mới nếu nó nói tiếp việc trước (vd 'sửa file đó', 'nhắn thêm cho anh ấy'). Màn "
                 "hình có thể đã thay đổi: screenshot kiểm tra trước khi thao tác. Yêu cầu mới "
                 "là việc khác thì bỏ qua ngữ cảnh này."]
        for index, item in enumerate(history, 1):
            minutes = int((now - item["at"]) // 60)
            lines.append(f"{index}. ({minutes} phút trước) Yêu cầu: {item['request']}\n"
                         f"   Kết quả: {item['result'] or '(không có)'}")
        return "\n".join(lines)

    def _remember(self, task_id: str, text: str) -> None:
        final = self._last_final if (self._last_final or {}).get("task_id") == task_id else {}
        result = final.get("result") or final.get("error") or ""
        if not isinstance(result, str):
            result = str(result)
        self._history.append({"at": time.monotonic(), "request": text,
                              "result": clip(result, CONTEXT_RESULT_CHARS),
                              "groups": self._task_groups})
        self._history = self._history[-max(CONTEXT_TURNS, 1):]

    async def on_chat(self, msg: dict):
        text = msg.get("text", "").strip()
        task_id = msg.get("task_id") or f"task-{int(time.time())}"
        if not text:
            return
        if self._choice is not None and not self._choice.done():
            self._choice.set_result(text)   # the answer to ask_user, not a new task
            return
        if self._active and not self._active.done():
            await self._ui({"type": "error", "task_id": task_id,
                                      "error": "một tác vụ khác đang chạy"})
            return
        if text.casefold().strip(" .!") in RESET_COMMANDS:
            self._history.clear()
            await self._ui({"type": "done", "task_id": task_id,
                            "result": "Đã xoá ngữ cảnh các yêu cầu trước."})
            return
        self._active = asyncio.create_task(self.run_task(task_id, text))

    async def on_cancel(self, msg: dict):
        if self._active and not self._active.done():
            self._active.cancel()   # stops LLM waits, confirms and pending clicks now
        # Tell the device to flush its queue too, not just stop the agent.
        try:
            await self._ui({"type": "cancel", "task_id": msg.get("task_id", "")})
        except Exception:  # noqa: BLE001
            pass
        await self._ui({"type": "cancel_ack", "task_id": msg.get("task_id", "")})

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
        self._shot_size = (width, height)
        self._scr_w, self._scr_h = self.relay.frame_sw, self.relay.frame_sh
        return annotated, (width, height)

    async def _progress(self, task_id: str, step: int, message: str):
        await self._ui({"type": "progress", "task_id": task_id,
                                  "step": step, "max_steps": MAX_STEPS, "message": message})

    def _record_verified_cache(self, text: str, skill_names: list[str]) -> None:
        """Persist only workflows whose final state was seen on a screenshot, and
        that never got stuck: a hint replaying wasted or wrong steps does harm."""
        if self._stuck_warned or not is_verified_workflow(self._executed_steps):
            return
        try:
            self.task_cache.record_verified(text, skill_names, self._executed_steps,
                                            device_id=self.device_id)
            log("workflow cache: saved verified task")
        except Exception as exc:  # noqa: BLE001
            log(f"workflow cache: save failed: {exc}")

    async def run_task(self, task_id: str, text: str):
        """Run one request; whatever happens the UI gets a final event. Time spent
        waiting for the user's answer does not count toward TASK_TIMEOUT."""
        agent_log.set_task(task_id, self.device_id)   # inherited by the inner task
        self._task_groups = ()
        inner = asyncio.ensure_future(self._execute(task_id, text))
        self._deadline = time.monotonic() + TASK_TIMEOUT
        try:
            while not inner.done():
                now = time.monotonic()
                waiting = self._user_wait_since is not None
                if not waiting and now >= self._deadline:
                    inner.cancel()
                    await asyncio.wait({inner})
                    raise asyncio.TimeoutError
                await asyncio.wait({inner}, timeout=0.5 if waiting else self._deadline - now)
            inner.result()
        except asyncio.TimeoutError:
            log(f"[{task_id}] task timeout after {TASK_TIMEOUT}s")
            await self._ui({"type": "done", "task_id": task_id,
                                      "result": "hết thời gian"})
        except asyncio.CancelledError:
            inner.cancel()
            await asyncio.wait({inner})
            log(f"[{task_id}] cancelled")
            await self._ui({"type": "done", "task_id": task_id,
                                      "result": "đã huỷ"})
        except Exception as e:  # noqa: BLE001 - never die without telling the UI
            log(f"[{task_id}] task crashed:\n{traceback.format_exc()}")
            await self._ui({"type": "error", "task_id": task_id,
                                      "error": f"lỗi nội bộ: {e}"})
        finally:
            self._remember(task_id, text)

    async def _execute(self, task_id: str, text: str):
        recent = self._recent_history()
        # Groups the request names by itself, before any context is carried over.
        named = gated_groups(build_system_prompt(text)[1])
        history = [item for item in recent if self._related(item, text, named)]
        carry = history[-1]["groups"] if history else ()
        sys_prompt, skill_names = build_system_prompt(text, carry)
        self._task_groups = gated_groups(skill_names)
        self._task_text = text
        self._executed_steps = []
        self._approved_names = set()
        self._search_open = False
        self._search_query = ""
        self._fail_counts = {}
        self._stuck_warned = False
        messages = [{"role": "system", "content": sys_prompt}]
        if history:
            messages.append({"role": "user", "content": self._history_note(history) + NT})
            log(f"[{task_id}] ngữ cảnh: {len(history)}/{len(recent)} yêu cầu trước liên quan, "
                f"nhóm skill {carry}")
        elif recent:
            log(f"[{task_id}] ngữ cảnh: {len(recent)} yêu cầu trước không liên quan -> bỏ")
        messages.append({"role": "user", "content": text + NT})
        cache_match = self.task_cache.retrieve(text, skill_names, device_id=self.device_id)
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
        if FIRST_LOOK and (self._task_groups or any(n.startswith("windows/") for n in skill_names)):
            await self._first_look(task_id, messages)
        empties = 0        # consecutive fully-empty LLM replies
        delivered = False  # has the agent actually typed/sent anything yet?
        deliver_nudges = 0 # times we pushed it to finish the "send" part
        send_checked = False  # already forced a screenshot after the send?
        watch = ProgressWatch(STUCK_LIMIT)   # actions that change nothing on screen
        # Reading a document or chat page by page: keep each page's text in the
        # conversation and stop once moving shows nothing new.
        reader = (ReadingTracker() if any(n in READING_SKILLS for n in skill_names)
                  and not _needs_delivery(text) else None)
        self._notes_chars, self._notes_recent = 0, []
        self._note_dedupe = ReadingTracker()
        force_answer = False
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
                                      tools=None if force_answer else TOOLS, think=THINK,
                                      options={"num_ctx": NUM_CTX})
                dt = time.monotonic() - t0
            except asyncio.TimeoutError:
                log(f"[{task_id}] LLM timeout sau {LLM_TIMEOUT}s (Ollama kẹt/đang tải model?)")
                await self._ui({"type": "error", "task_id": task_id,
                                          "error": f"LLM quá {LLM_TIMEOUT}s không phản hồi "
                                                   "(kiểm tra Ollama: 'ollama ps', VRAM, num_ctx)"})
                return
            except Exception as e:  # noqa: BLE001
                log(f"[{task_id}] LLM error: {e}")
                await self._ui({"type": "error", "task_id": task_id,
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
                    # Sent but never looked: check the screen before reporting. Not
                    # gated on _is_ask_or_blocked: a success report that quotes the
                    # message can contain "?".
                    if (needs_deliver and delivered and not send_checked
                            and self._executed_steps[-1]["tool"] != "screenshot"):
                        send_checked = True
                        if await self._attach_send_proof(task_id, messages):
                            continue
                        result += " (chưa xác minh được trên màn hình)"
                    log(f"[{task_id}] DONE: {result[:160]}")
                    self._record_verified_cache(text, skill_names)
                    await self._ui({"type": "done", "task_id": task_id,
                                              "result": result})
                    return
                # Completely empty reply: nudge once or twice before giving up,
                # instead of ending the task silently.
                empties += 1
                if empties >= 3:
                    log(f"[{task_id}] DONE: empty {empties}x, giving up")
                    await self._ui({"type": "done", "task_id": task_id,
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
                call_key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False, default=str))
                search_was_open = self._search_open
                if (name not in NEVER_BLOCKED
                        and self._fail_counts.get(call_key, 0) >= REPEAT_FAIL_LIMIT):
                    log(f"[{task_id}] chặn lặp lại lệnh đã lỗi {REPEAT_FAIL_LIMIT} lần: {name}")
                    result = ("error: KHÔNG chạy: lệnh này đã lỗi y hệt "
                              f"{REPEAT_FAIL_LIMIT} lần. Đổi cách khác (screenshot xem vì sao, "
                              "dùng thao tác khác) hoặc ask_user.")
                else:
                    try:
                        result = await self._run_tool(task_id, name, args)
                    except Exception as e:  # noqa: BLE001 - e.g. malformed model arguments
                        log(f"[{task_id}] tool {name} crashed:\n{traceback.format_exc()}")
                        result = f"error: không chạy được {name}: {e}"
                    if (result.startswith("error") and name not in NEVER_BLOCKED
                            and not any(m in result for m in GUIDANCE_ERRORS)):
                        count = self._fail_counts.get(call_key, 0) + 1
                        self._fail_counts[call_key] = count
                        if count >= REPEAT_FAIL_LIMIT:
                            result += (f" (Đã lỗi y hệt {count} lần: KHÔNG lặp lại lệnh này nữa.)")
                if result.startswith("error"):
                    pass
                elif name in SCREEN_MUTATING_TOOLS:
                    watch.action(name)
                    if reader:
                        reader.action(name)
                elif name == "screenshot" and self._raw_frame:
                    warning = watch.screenshot(self._raw_frame)
                    if warning:
                        self._stuck_warned = True
                        log(f"[{task_id}] màn hình không đổi ({watch.stuck}/{STUCK_LIMIT})")
                        result = f"{result} {warning}"
                    if reader and not search_was_open:
                        note, done_reading = await self._reading_note(task_id, reader)
                        result = f"{result}{note}"
                        if done_reading and not force_answer:
                            force_answer = True
                            log(f"[{task_id}] đã đọc hết (không còn chữ mới) -> buộc trả lời")
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
                if self._pending_image is not None:
                    note = self._pending_note or (
                            f"Ảnh màn hình {self._img_w}x{self._img_h}, có LƯỚI TOẠ ĐỘ màu đỏ: "
                            "các vạch dọc/ngang cách nhau 100 px, có ghi số pixel. "
                            "Hãy ĐỌC toạ độ của phần tử theo lưới này (nội suy giữa hai vạch "
                            "gần nhất), đừng đoán. Cho move/click theo pixel trên ảnh; hệ "
                            "thống tự quy đổi ra màn hình thật.")
                    messages.append({"role": "user", "content": note + NT,
                                     "images": [self._pending_image]})
                    self._pending_image = None
                    self._pending_note = None
                if reader and self._notes_chars > NOTES_MAX_CHARS:
                    await self._compress_notes(task_id, messages)

            if force_answer and calls:
                messages.append({"role": "user", "content":
                    "Đã đọc HẾT tài liệu: các trang cuối không còn chữ mới. KHÔNG cuộn hay mở "
                    "lại. Viết câu trả lời cuối NGAY bây giờ, dựa trên TẤT CẢ ghi chú các trang "
                    "đã ghi ở trên (và 'Ghi chú tổng hợp' nếu có), theo đúng thứ tự." + NT})

            if watch.exhausted:
                log(f"[{task_id}] dừng: màn hình không đổi {watch.stuck} lần liên tiếp")
                await self._ui({"type": "done", "task_id": task_id, "result":
                    f"Dừng vì bị kẹt: màn hình không thay đổi sau {watch.stuck} lần thao tác "
                    f"liên tiếp. {self._steps_summary()}"})
                return

        await self._ui({"type": "done", "task_id": task_id,
                        "result": f"Đạt giới hạn {MAX_STEPS} bước, chưa xong. "
                                  f"{self._steps_summary()}"})

    def _steps_summary(self, count: int = 6) -> str:
        """The last few tool calls, so a stopped task says where it got stuck."""
        parts = []
        for item in self._executed_steps[-count:]:
            args = ", ".join(f"{k}={str(v)[:30]}" for k, v in item["arguments"].items())
            parts.append(f"{item['tool']}({args})")
        return ("Các bước cuối: " + "; ".join(parts)) if parts else ""

    async def _run_tool(self, task_id: str, name: str, args: dict) -> str:
        if name not in TOOL_NAMES:
            return f"error: tool '{name}' không được phép"
        if name == "screenshot":
            self._search_open = False
            return await self._screenshot(task_id, force=True, attach=True)
        if name == "hotkey" and str(args.get("combo", "")).lower().replace(" ", "") == "win+s":
            opened = await self._already_open(task_id)
            if opened:
                return (f"error: KHÔNG mở lại: thanh tiêu đề đang là '{opened}', file cần dùng ĐÃ MỞ. "
                        "Làm tiếp việc đọc/sửa ngay trên cửa sổ này.")
            self._search_open = True
            self._search_query = ""
        if name == "type" and self._search_open:
            self._search_query = str(args.get("text") or "")
        if (name == "key" and self._search_open
                and str(args.get("name", "")).lower() == "enter"):
            return await self._look_before_enter(task_id)
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
        if name == "zoom":
            self._search_open = False   # looking at the results counts
            return await self._zoom(task_id, args)
        if name == "ask_user":
            return await self._ask_user_tool(task_id, args)
        # Image-space coords from the model: refine by zoom, then map to screen.
        image_point = None
        if name in ("move", "click") and "x" in args and "y" in args:
            ix, iy = int(args["x"]), int(args["y"])
            ix, iy = await self._refine_point(task_id, ix, iy)
            image_point = (ix, iy)
            self._mark_click(task_id, ix, iy, f"{name}_click")
            args = dict(args)
            if self._img_w and self._scr_w:
                args["x"] = round(ix * self._scr_w / self._img_w)
                args["y"] = round(iy * self._scr_h / self._img_h)
                log(f"[{task_id}] {name} img({ix},{iy}) -> screen({args['x']},{args['y']})")
            else:
                args["x"], args["y"] = ix, iy
        if name == "type" and isinstance(args.get("text"), str):
            args = dict(args, text=self._prepare_text(args["text"]))
        if is_dangerous(name, args) and not self._safe_multiline(name, args):
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
            if image_point:
                # Same units the model used: the device detail is in screen pixels.
                return f"ok: {name} tại điểm ({image_point[0]},{image_point[1]}) trên ảnh"
            return f"ok {detail}".strip()
        return f"error: {res.get('error', 'thất bại')}"

    async def _attach_send_proof(self, task_id: str, messages: list) -> bool:
        """Screenshot the result of a send and make the model judge from it."""
        log(f"[{task_id}] chưa xác minh sau khi gửi -> tự chụp màn hình")
        result = await self._screenshot(task_id, force=True, attach=True)
        if self._pending_image is None:
            log(f"[{task_id}] không chụp được để xác minh: {result[:120]}")
            return False
        self._executed_steps.append({"tool": "screenshot", "arguments": {}, "result": result})
        messages.append({"role": "user", "images": [self._pending_image], "content":
            "Đây là màn hình SAU khi gửi. Chỉ báo thành công nếu THẤY nội dung vừa gửi đã "
            "xuất hiện. Không thấy thì nói rõ là chưa xác minh được. KHÔNG gửi lại." + NT})
        self._pending_image = None
        return True

    @contextlib.contextmanager
    def _user_wait(self):
        """Pause the task timeout while the user is answering."""
        started = time.monotonic()
        self._user_wait_since = started
        try:
            yield
        finally:
            self._user_wait_since = None
            self._deadline += time.monotonic() - started

    async def _ask_choice(self, task_id: str, question: str, options: list) -> tuple:
        """Show a question with numbered options in the chat and wait for the
        reply (typed, or a button on firmware with the new page). Returns
        (index or None, reply text); the text is "" when nobody answered."""
        lines = [question] + [f"{i}. {o}" for i, o in enumerate(options, 1)]
        lines.append("(Trả lời bằng số thứ tự.)" if options else "(Trả lời trong ô chat.)")
        self._choice = asyncio.get_running_loop().create_future()
        await self._ui({"type": "assistant", "task_id": task_id,
                                  "content": "\n".join(lines), "options": options})
        log(f"[{task_id}] hỏi người dùng: {question} {options}")
        try:
            with self._user_wait():
                reply = await asyncio.wait_for(self._choice, CHOICE_TIMEOUT)
        except asyncio.TimeoutError:
            log(f"[{task_id}] người dùng không trả lời sau {CHOICE_TIMEOUT:.0f}s")
            return None, ""
        finally:
            self._choice = None
        index = parse_choice(reply, options)
        log(f"[{task_id}] người dùng trả lời: {reply!r} -> {index}")
        return index, reply

    async def _ask_user_tool(self, task_id: str, args: dict) -> str:
        question = str(args.get("question") or "").strip()
        options = args.get("options") or []
        if isinstance(options, str):
            options = [options]
        options = [str(o).strip() for o in options if str(o).strip()][:8]
        if not question:
            return "error: thiếu question"
        index, reply = await self._ask_choice(task_id, question, options)
        if not reply:
            return ("error: người dùng không trả lời. ĐỪNG tự chọn thay; kết thúc và nói rõ "
                    "đang chờ người dùng chọn.")
        if index is not None:
            return f"ok: người dùng chọn {index + 1}. {options[index]}"
        return f"ok: người dùng trả lời: {reply}"

    async def _type_text(self, task_id: str, text: str) -> str:
        """Type text of any length. The device protocol is one line per command, so
        each line is typed in chunks and line breaks are pressed as keys: shift+enter
        (a new line that does not send, in chat apps) or enter in Word. Stops at the
        first failure and says so, because the model must not press Enter on half
        a message."""
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if "word" in self._task_groups:
            newline = ("key", {"name": "enter"})
        else:
            newline = ("hotkey", {"combo": "shift+enter"})
        steps = []
        try:
            for index, line in enumerate(lines):
                if index:
                    steps.append(newline)
                steps += [("type", {"text": chunk}) for chunk in split_for_type(line) if chunk]
        except ValueError as e:
            return f"error: không gõ được: {e}"
        for index, (tool, args) in enumerate(steps, start=1):
            failure = ""
            try:
                res = await self.relay.call_device(tool, args, task_id, timeout=DEVICE_TIMEOUT)
                if not res.get("ok"):
                    failure = res.get("error", "thất bại")
            except asyncio.TimeoutError:
                failure = "thiết bị không phản hồi (timeout), KHÔNG rõ đoạn này đã gõ chưa"
            except Exception as e:  # noqa: BLE001
                failure = str(e)
            if failure:
                self._invalidate_screen_cache()
                return (f"error: lỗi khi gõ đoạn {index}/{len(steps)}: {failure}. ĐỪNG nhấn Enter; "
                        "screenshot kiểm tra ô nhập, xoá phần đã gõ rồi gõ lại.")
        self._invalidate_screen_cache()
        breaks = len(lines) - 1
        return f"ok đã gõ {len(text)} ký tự" + (f", {breaks} lần xuống dòng" if breaks else "")

    async def _capture_region(self, task_id: str, x: int, y: int, w: int, h: int) -> tuple:
        """Capture one screen rectangle. Returns (image, scale, native): image pixel
        (px, py) is screen (x + px / scale, y + py / scale). native is False when the
        PC agent or firmware is older and the region was cut out of a full (scaled)
        screenshot instead. (None, 0.0, False) on failure."""
        request_id = new_request_id()
        try:
            waiter = self.relay.arm_frame(request_id)
            res = await self.relay.call_device("screenshot", {"region": [x, y, w, h]}, task_id,
                                               timeout=DEVICE_TIMEOUT, request_id=request_id)
            if not res.get("ok"):
                log(f"[{task_id}] chụp vùng lỗi: {res.get('error', '?')}")
                return None, 0.0, False
            image = await asyncio.wait_for(waiter, 12)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - timeout, relay down, old fake relay
            log(f"[{task_id}] chụp vùng lỗi: {type(e).__name__}: {e}")
            return None, 0.0, False
        size = image_size(image) if image else None
        if not size:
            return None, 0.0, False
        detail = (res.get("result") or {}).get("detail", "") or ""
        if "region" in detail:
            return image, size[0] / w, True
        log(f"[{task_id}] thiết bị trả ảnh toàn màn hình thay vì vùng (detail: {detail[:60]!r}) "
            "-> cắt tạm từ ảnh nhỏ")
        scale = size[0] / (self.relay.frame_sw or size[0])
        crop = crop_image(image, (x * scale, y * scale, (x + w) * scale, (y + h) * scale))
        return crop, scale, False

    async def _zoom(self, task_id: str, args: dict) -> str:
        """The zoom tool: a region at native resolution plus the text read in it."""
        screen_w, screen_h = self.relay.frame_sw, self.relay.frame_sh
        if not screen_w:
            await self._screenshot(task_id, force=True, attach=False)
            screen_w, screen_h = self.relay.frame_sw, self.relay.frame_sh
            if not screen_w:
                return "error: chưa biết kích thước màn hình (screenshot trước)"
        box, area = args.get("box"), str(args.get("area") or "").lower()
        if isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box):
            if not self._shot_size[0]:
                return "error: chưa có screenshot để hiểu toạ độ box; dùng area"
            x, y, w, h = image_box_to_screen(box, *self._shot_size, screen_w, screen_h)
            label = f"box {list(box)}"
        elif area in ZOOM_AREAS:
            x, y, w, h = fraction_box(ZOOM_AREAS[area], screen_w, screen_h)
            label = area
        else:
            return ("error: cần area (" + ", ".join(ZOOM_AREAS) + ") hoặc box [x0,y0,x1,y1]")
        image, _scale, native = await self._capture_region(task_id, x, y, w, h)
        if image is None:
            return "error: không chụp được vùng này"
        self._save(task_id, f"zoom_{area or 'box'}", image,
                   "jpg" if image[:2] == b"\xff\xd8" else "png")
        text = ""
        if OCR_ENABLE:
            try:
                items = await asyncio.get_running_loop().run_in_executor(
                    None, ocr.run_ocr, image if native else upscale(image), OCR_LANG)
                text = ocr_lines(items) if items else ""
            except Exception as e:  # noqa: BLE001
                log(f"[{task_id}] zoom OCR lỗi: {e}")
        how = ("độ phân giải thật" if native else
               "cắt từ ảnh toàn màn hình vì thiết bị chưa hỗ trợ chụp vùng")
        log(f"[{task_id}] zoom {label} -> màn hình ({x},{y}) {w}x{h}, {how}")
        self._pending_image = image
        self._pending_note = (f"Ảnh PHÓNG TO vùng {label} (màn hình x {x}..{x + w}, y {y}..{y + h}), "
                              f"{how}. Chỉ để ĐỌC; muốn bấm thì dùng tap với keywords là chữ "
                              "đọc được ở đây.")
        return (f"ok: đã chụp vùng {label} ({how}), ảnh ở lượt sau."
                + (f" Chữ trong vùng:\n{text}" if text else " OCR không đọc được chữ nào."))

    def _wanted_files(self) -> list[str]:
        """Comparable keys of the file names in this request and the recent ones
        ("Ho-so-du-an.docx" -> "hosoduan"): names with - or _ or a Word extension."""
        texts = [self._task_text] + [h["request"] for h in self._recent_history()]
        keys = []
        for text in texts:
            for token in re.findall(r"[\w.\-]{6,}", text or ""):
                low = token.lower()
                if "-" in low or "_" in low or low.endswith((".docx", ".doc")):
                    key = _file_key(re.sub(r"\.docx?$", "", low))
                    if len(key) >= 6 and key not in keys:
                        keys.append(key)
        return keys

    async def _read_title(self, task_id: str) -> str | None:
        """OCR of the title bar of the window in front, or None if unavailable."""
        screen_w, screen_h = self.relay.frame_sw, self.relay.frame_sh
        if not (screen_w and OCR_ENABLE):
            return None
        x, y, w, h = fraction_box(ZOOM_AREAS["title"], screen_w, screen_h)
        image, _scale, native = await self._capture_region(task_id, x, y, w, h)
        if image is None:
            return None
        try:
            items = await asyncio.get_running_loop().run_in_executor(
                None, ocr.run_ocr, image if native else upscale(image), OCR_LANG)
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] đọc thanh tiêu đề lỗi: {e}")
            return None
        return " ".join(it["text"] for it in items or []).strip()

    @staticmethod
    def _related(item: dict, text: str, named: tuple) -> bool:
        """Is an earlier request relevant to this one? A request naming no app is a
        follow-up and keeps its context. Otherwise the earlier one must share the
        app, be referred to ("gửi tóm tắt trên"), or name the same person or file."""
        if not named:
            return True
        if set(item["groups"]) & set(named):
            return True
        low = f" {text.lower()} "
        if any(re.search(rf"(?<!\w){re.escape(word)}(?!\w)", low) for word in REFERENCE_WORDS):
            return True
        people = {" ".join(p) for p in requested_persons(text)}
        if people & {" ".join(p) for p in requested_persons(item["request"])}:
            return True
        files = {_file_key(t) for t in re.findall(r"[\w.\-]*[-_][\w.\-]+", text)}
        return any(_file_key(t) in files
                   for t in re.findall(r"[\w.\-]*[-_][\w.\-]+", item["request"]))

    def _app_in_front(self, title: str) -> tuple:
        """(app the task needs, whether the title shows it). ("", True) when the task
        needs no particular app."""
        if not self._task_groups:
            return "", True
        key = _file_key(title)
        for group in self._task_groups:
            hints = APP_TITLE_HINTS.get(group, ())
            if any(hint in key for hint in hints) or (
                    group == "word" and any(k in key for k in self._wanted_files())):
                return group, True
        return self._task_groups[0], False

    async def _first_look(self, task_id: str, messages: list) -> None:
        """Show the model the real screen before its first step, with the window
        in front, so it cannot act on stale context or a cached workflow."""
        await self._screenshot(task_id, force=True, attach=True)
        if self._pending_image is None:
            log(f"[{task_id}] không chụp được màn hình lúc bắt đầu")
            return
        image, self._pending_image = self._pending_image, None
        title = await self._read_title(task_id)
        lines = ["Ảnh màn hình HIỆN TẠI, hệ thống tự chụp khi bắt đầu yêu cầu này. Ngữ cảnh "
                 "và workflow cũ có thể đã sai: chỉ tin vào ảnh."]
        if title:
            lines.append(f"Thanh tiêu đề cửa sổ đang ở trước: '{title[:80]}'.")
            app, in_front = self._app_in_front(title)
            if app and not in_front:
                lines.append(f"Ứng dụng cần dùng ({APP_LABELS.get(app, app)}) KHÔNG ở trước. Đưa "
                             "nó lên trước (hotkey win+s → type tên ứng dụng → screenshot → "
                             "mở đúng mục) và xác minh, TRƯỚC khi bấm hay gõ bất cứ gì.")
            elif app:
                lines.append(f"Ứng dụng cần dùng ({APP_LABELS.get(app, app)}) đang ở trước.")
        log(f"[{task_id}] nhìn màn hình đầu tiên: tiêu đề {title!r}")
        lines.append(f"Ảnh {self._img_w}x{self._img_h} có lưới toạ độ đỏ mỗi 100 px.")
        messages.append({"role": "user", "content": " ".join(lines) + NT, "images": [image]})

    async def _already_open(self, task_id: str) -> str:
        """The window title when it shows the Word file this task is about (so it
        must not be searched for and opened again), else ""."""
        if "word" not in self._task_groups:
            return ""
        wanted = self._wanted_files()
        if not wanted:
            return ""
        title = await self._read_title(task_id)
        if not title:
            return ""
        title_key = _file_key(title)
        for key in wanted:
            if key in title_key or (len(key) > 20 and key[:20] in title_key):
                log(f"[{task_id}] file đã mở ('{title[:60]}') -> chặn mở lại")
                return title[:80]
        return ""

    async def _reading_note(self, task_id: str, reader) -> tuple[str, bool]:
        """Note what the page just captured says, appended to the screenshot's tool
        result (text is never pruned from the conversation, unlike screenshots), and
        tell whether reading has reached the end (twice in a row nothing new after
        moving). See READ_NOTES."""
        lines = None
        if READ_NOTES in ("ocr", "both") and OCR_ENABLE:
            items = await self._ocr_items(task_id)
            if items:
                lines = ocr_lines(items, limit=100000).split("\n")
        if lines is not None:
            fresh, nothing_new = reader.page(lines)
            if nothing_new:
                return self._end_note(task_id, reader)
            if READ_NOTES == "ocr":
                return self._note_block(OCR_NOTE_HEAD, fresh), False
        if READ_NOTES == "ocr":
            return "", False                     # OCR unavailable: nothing to note
        transcript = await self._transcribe_page(task_id)
        if transcript is None:
            return "", False                     # the call failed: no verdict either
        page_lines = [line.strip() for line in transcript.split("\n") if line.strip()]
        if lines is None:                        # no OCR: the model's text decides
            fresh, nothing_new = reader.page(page_lines)
            if nothing_new:
                return self._end_note(task_id, reader)
        else:
            fresh, _ = self._note_dedupe.page(page_lines)
        return self._note_block(MODEL_NOTE_HEAD, fresh), False

    def _end_note(self, task_id: str, reader) -> tuple[str, bool]:
        log(f"[{task_id}] trang không có chữ mới ({reader.stale})")
        return (" KHÔNG có chữ mới so với các trang đã đọc: đã tới CUỐI (hoặc cuộn không tác "
                "dụng). Đừng cuộn tiếp; trả lời dựa trên ghi chú các trang đã ghi."), \
            reader.stale >= 2

    def _note_block(self, head: str, lines: list[str]) -> str:
        if not lines:
            return " (Trang này không có nội dung mới đáng ghi.)"
        text = "\n".join(lines)
        if len(text) > 3000:
            text = text[:3000] + " …"
        self._notes_chars += len(text)
        self._notes_recent = (self._notes_recent + lines)[-15:]
        return f"\n{head}:\n{text}"

    async def _transcribe_page(self, task_id: str) -> str | None:
        """The vision model copies the page's text. "" when it finds nothing new,
        None when the call fails."""
        if not self._raw_frame:
            return None
        tail = "\n".join(self._notes_recent[-8:])
        prompt = (
            "Ảnh là màn hình đang mở một tài liệu hoặc cuộc trò chuyện. Chép lại NGUYÊN VĂN "
            "phần nội dung chính theo đúng thứ tự từ trên xuống: giữ tiêu đề mục, số thứ tự, "
            "số liệu, tên riêng, đúng dấu tiếng Việt; mỗi đoạn một dòng. Bỏ menu, thanh "
            "công cụ, thanh tiêu đề. Tin nhắn: ghi 'Họ:' cho bong bóng bên trái, 'Mình:' cho "
            "bên phải. Không thêm lời bình, không tóm tắt."
            + (f"\nBỎ QUA các dòng đã chép trước đó (cuối ghi chú):\n{tail}" if tail else "")
            + "\nNếu không có gì mới, chỉ trả về: KHÔNG CÓ NỘI DUNG MỚI /no_think")
        t0 = time.monotonic()
        try:
            resp = await llm.chat(model=MODEL, think=False, options={"num_ctx": NUM_CTX},
                                  messages=[{"role": "user", "content": prompt,
                                             "images": [self._raw_frame]}])
            text = (resp["message"].get("content") or "").strip()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] chép trang lỗi: {e}")
            return None
        log(f"[{task_id}] model chép trang: {len(text)} ký tự, {time.monotonic() - t0:.1f}s")
        if NOTHING_NEW in text.upper():
            # Only "nothing new" when that is (almost) all it says: a model may add
            # the phrase after copying a whole page.
            rest = "\n".join(line for line in text.split("\n")
                             if NOTHING_NEW not in line.upper()).strip()
            if len(rest) < 15:
                return ""
            text = rest
        return text[:4000]

    async def _summarize_notes(self, notes: str) -> str:
        prompt = (f"Tóm gọn ghi chú đọc tài liệu sau còn khoảng {NOTES_MAX_CHARS // 2} ký tự. "
                  "Giữ đúng thứ tự các mục, tiêu đề, số liệu, tên riêng; bỏ phần lặp. Chỉ trả "
                  f"về bản tóm gọn.\n\n{notes} /no_think")
        resp = await llm.chat(model=MODEL, think=False, options={"num_ctx": NUM_CTX},
                              messages=[{"role": "user", "content": prompt}])
        return (resp["message"].get("content") or "").strip()

    async def _compress_notes(self, task_id: str, messages: list) -> None:
        """Replace the page notes collected so far by one condensed summary, so a
        long document does not overflow the model's context."""
        holders, parts = [], []
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            content = message.get("content") or ""
            if message.get("role") == "user" and content.startswith(SUMMARY_HEAD):
                parts.append(content[len(SUMMARY_HEAD):].strip())
                holders.append((index, None))
                continue
            if message.get("role") != "tool":
                continue
            for head in (MODEL_NOTE_HEAD, OCR_NOTE_HEAD):
                marker = f"\n{head}:"
                if marker in content:
                    base, note = content.split(marker, 1)
                    parts.append(note.strip())
                    holders.append((index, base))
                    break
        if not parts:
            return
        try:
            summary = await self._summarize_notes("\n".join(parts))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 - keep the notes as they are
            log(f"[{task_id}] tóm gọn ghi chú lỗi: {e}")
            return
        if not summary:
            return
        for index, base in reversed(holders):
            if base is None:
                del messages[index]
            else:
                messages[index]["content"] = (base + " [ghi chú trang này đã gộp vào "
                                              "'Ghi chú tổng hợp' bên dưới]")
        messages.append({"role": "user", "content": f"{SUMMARY_HEAD}\n{summary}" + NT})
        log(f"[{task_id}] tóm gọn ghi chú: {self._notes_chars} -> {len(summary)} ký tự")
        self._notes_chars = len(summary)

    def _prepare_text(self, text: str) -> str:
        """Chat apps show Markdown literally: drop its markup there."""
        if not ({"zalo", "facebook"} & set(self._task_groups)):
            return text
        lines = []
        for line in text.replace("\r\n", "\n").split("\n"):
            line = re.sub(r"^\s*#+\s*", "", line)
            line = re.sub(r"^(\s*)[*+]\s+", r"\1- ", line)
            lines.append(line.replace("**", "").replace("__", "").replace("`", ""))
        return "\n".join(lines)

    def _safe_multiline(self, name: str, args: dict) -> bool:
        """Line breaks are only risky where Enter runs a command (Run box,
        terminal). In a chat app or Word they become shift+enter / enter."""
        if name != "type" or not self._task_groups:
            return False
        flat = str(args.get("text", "")).replace("\r", " ").replace("\n", " ")
        return not is_dangerous("type", {"text": flat})

    async def _top_search_result(self, task_id: str) -> str:
        """Name of the first result under "Best match" in Windows Search, or ""."""
        items = await self._ocr_items(task_id) if OCR_ENABLE else None
        if not items:
            return ""
        labels = [it for it in items
                  if max(_sim(k, it["text"]) for k in SEARCH_TOP_LABELS) >= 0.8]
        if not labels:
            return ""
        label = min(labels, key=lambda it: it["cy"])
        below = [it for it in items
                 if 0 < it["cy"] - label["cy"] <= 70 and abs(it["x0"] - label["x0"]) <= 160
                 and it is not label]
        if not below:
            return ""
        return min(below, key=lambda it: it["cy"])["text"].strip(" '\"")

    async def _look_before_enter(self, task_id: str) -> str:
        """Enter right after typing into Windows Search opens whatever happens to
        be first, often a web search or the wrong file. Show the results first."""
        self._search_open = False
        log(f"[{task_id}] Enter trong Windows Search khi chưa xem kết quả -> chụp trước")
        shot = await self._screenshot(task_id, force=True, attach=True)
        if self._pending_image is None:
            return f"error: chưa nhấn Enter; cần xem kết quả tìm kiếm trước ({shot})"
        top = await self._top_search_result(task_id)
        if top and self._search_query and _same_name(self._search_query, top):
            log(f"[{task_id}] kết quả đầu '{top}' khớp '{self._search_query}' -> nhấn Enter")
            try:
                res = await self.relay.call_device("key", {"name": "enter"}, task_id,
                                                   timeout=DEVICE_TIMEOUT)
            except Exception as e:  # noqa: BLE001
                return f"error: {e}"
            self._invalidate_screen_cache()
            if not res.get("ok"):
                return f"error: {res.get('error', 'thất bại')}"
            return (f"ok: kết quả đầu tiên '{top}' khớp từ khóa '{self._search_query}', đã nhấn "
                    "Enter để mở. Screenshot để xác minh.")
        return ("error: CHƯA nhấn Enter. Đã chụp kết quả tìm kiếm của Windows (ảnh ở lượt "
                "sau). Đúng MỘT file/app khớp và nó đứng đầu → `key enter`; nằm ở dòng khác → "
                "`tap` vào nó; nhiều kết quả giống nhau → `ask_user`; không có → thử từ khóa "
                "ngắn hơn. Không tự đặt đường dẫn.")

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
        await self._ui({"type": "confirm_request", "task_id": task_id,
                                  "confirm_id": cid, "tool": name, "arguments": args,
                                  "message": message or f"Xác nhận hành động: {name} {args}"})
        # on_confirm() resolves this future when the UI answers. No answer in 60s
        # -> deny (fail safe).
        try:
            with self._user_wait():
                return await asyncio.wait_for(fut, 60)
        except asyncio.TimeoutError:
            return False
        finally:
            self._confirm_waiters.pop(cid, None)
