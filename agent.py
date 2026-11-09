"""Linux AI agent: drives the Momo device through the relay using Ollama.

Flow per user request (a `chat` message relayed from the browser via the ESP32):
  LLM picks a tool -> allowlist/arg check -> (confirm if dangerous) ->
  call_device -> feed the device_result back to the LLM -> repeat, bounded by
  MAX_STEPS / TASK_TIMEOUT / per-tool retries, cancellable from the UI.

The agent never concludes success from "command accepted"; it is prompted to
call screenshot/verify and judge from the returned result. Screen *vision*
(feeding the uploaded frame to a vision model) is a follow-up: the frame is
uploaded by the PC to the ESP32 over the LAN, so routing it to this remote agent
needs an extra path (see README "Next: vision"). Until then the agent works
from device_result text and the user's instructions.

Env (.env): RELAY_URL, AGENT_TOKEN, CA_FILE, OLLAMA_MODEL, OLLAMA_HOST,
MAX_STEPS, TASK_TIMEOUT_SECONDS, MAX_RETRIES_PER_TOOL,
SCREEN_CACHE_TTL_SECONDS, OCR_CACHE_TTL_SECONDS.
"""
import asyncio
import glob
import hashlib
import os
import time
from pathlib import Path

import ollama
from dotenv import load_dotenv

import ocr
import ui_boxes
import web_search
from relay_client import RelayClient
from task_cache import TaskCache
from tools import TOOLS, TOOL_NAMES, is_dangerous

load_dotenv()

RELAY_URL = os.environ["RELAY_URL"]
AGENT_TOKEN = os.environ["AGENT_TOKEN"]
CA_FILE = os.environ.get("CA_FILE", "certs/ca.pem")
MODEL = os.environ.get("OLLAMA_MODEL", "qwen3:8b")
MAX_STEPS = int(os.environ.get("MAX_STEPS", "20"))
TASK_TIMEOUT = float(os.environ.get("TASK_TIMEOUT_SECONDS", "300"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES_PER_TOOL", "2"))
DEVICE_TIMEOUT = float(os.environ.get("DEVICE_CALL_TIMEOUT", "10"))
# Per LLM call. Ollama on CPU (or reloading an 8B model) can spike to 100% CPU
# for a while; this just stops a truly stuck call from hanging the task forever.
LLM_TIMEOUT = float(os.environ.get("OLLAMA_TIMEOUT", "180"))
# Ollama context window. Default 4096 is too small once the skills prompt + a
# screenshot image are included; raise it (uses more VRAM/KV cache).
NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "12288"))
# Qwen3 reads the /no_think soft switch from the USER turn (not the system
# prompt), so we append it there. Set NO_THINK=false to stop appending it.
NO_THINK = os.environ.get("NO_THINK", "true").lower() in ("1", "true", "yes")
NT = " /no_think" if NO_THINK else ""
# Coarse-to-fine click: after the model's rough click, crop+zoom that area and
# ask it to pin the exact point (an 8B VLM localises far better on a big crop).
REFINE_CLICK = os.environ.get("REFINE_CLICK", "true").lower() in ("1", "true", "yes")
# Save every image the agent handles (screenshots, grids, locate steps, and the
# point it actually clicked) to a folder, for after-the-fact inspection.
SAVE_IMAGES = os.environ.get("SAVE_IMAGES", "true").lower() in ("1", "true", "yes")
IMAGE_DIR = os.environ.get("IMAGE_DIR", "debug_images")
# OCR Set-of-Mark locate: find text regions, label them, let the model pick one
# by number (exact coords from OCR). Falls back to the 3x3 grid when OCR finds
# nothing or the target has no text. Needs paddleocr+paddlepaddle (CPU).
OCR_ENABLE = os.environ.get("OCR_ENABLE", "true").lower() in ("1", "true", "yes")
OCR_LANG = os.environ.get("OCR_LANG", "vi")
# web_search tool: let the model look things up on the Internet from the server
# (ddgs/tavily). Off -> the tool returns a disabled note. See web_search.py.
SEARCH_ENABLE = os.environ.get("SEARCH_ENABLE", "true").lower() in ("1", "true", "yes")
# (C) UI Automation: before any vision, ask the PC for a control's REAL screen
# coords by name (MomoBleAgent.ps1 "uia" command). Exact, scale-free. Falls
# through to OCR/marks/grid when the element isn't exposed (e.g. canvas-rendered).
UIA_ENABLE = os.environ.get("UIA_ENABLE", "true").lower() in ("1", "true", "yes")
UIA_TIMEOUT = float(os.environ.get("UIA_TIMEOUT", "8"))
# UIA Set-of-Mark: when name/type lookup misses, ask the PC to number every
# interactive control on a screenshot (exact boxes from the accessibility tree)
# and let the LLM pick one; the number->coords map stays on the PC. Great for
# browser UIs that expose a11y. Falls through to OCR when no controls / no pick.
UIA_MARKS_ENABLE = os.environ.get("UIA_MARKS_ENABLE", "true").lower() in ("1", "true", "yes")
# (B) UI-TARS grounding: a GUI-specialist model does the "where to click" job
# while qwen stays the brain (instruction/planning/chat). On a tap we send the
# screenshot + element description to UI-TARS, which returns click coords. Only
# one model runs at a time: Ollama swaps UI-TARS in (a few seconds on a 12GB GPU,
# evicting qwen) then swaps qwen back for the next reasoning call. Create the
# model in Ollama first (see Modelfile) and set UITARS_MODEL to its tag.
UITARS_ENABLE = os.environ.get("UITARS_ENABLE", "false").lower() in ("1", "true", "yes")
UITARS_MODEL = os.environ.get("UITARS_MODEL", "ui-tars")
UITARS_NUM_CTX = int(os.environ.get("UITARS_NUM_CTX", "4096"))
UITARS_TIMEOUT = float(os.environ.get("UITARS_TIMEOUT", "90"))
# Longest side the frame is scaled to before sending, so the returned pixel
# coords map cleanly (Ollama's own resize can't shift a frame already in budget).
UITARS_MAX_SIDE = int(os.environ.get("UITARS_MAX_SIDE", "1288"))
# Send the frame at the REAL screen resolution so UI-TARS returns coords directly
# in screen space (no scaling, 'auto' always right). Trade-off: a full 1920x1080
# (~2M px) may exceed the model's vision budget and trigger an INTERNAL resize ->
# coords land in an unknown smaller space and go wrong. Leave off unless a test
# shows clicks are accurate at full res. Off = scale to UITARS_MAX_SIDE (safe).
UITARS_FULLRES = os.environ.get("UITARS_FULLRES", "false").lower() in ("1", "true", "yes")
# Coordinate convention UI-TARS emits: 'image' = pixels of the sent frame,
# 'norm1000' = 0..1000 normalised, 'auto' = guess (>1000 -> image, else norm1000).
# Calibrate once from the 'tars raw=... -> screen' log, then pin it explicitly.
UITARS_COORD = os.environ.get("UITARS_COORD", "auto").lower()
# Plan-and-execute: for a multi-step request, make one text-only LLM call first to
# decompose it into a numbered step list, then keep that list in context as a
# checklist. Cheap (~1 call), no extra VRAM. Simple tasks skip it (see _needs_plan).
PLAN_ENABLE = os.environ.get("PLAN_ENABLE", "true").lower() in ("1", "true", "yes")
# Set-of-Mark locate: between OCR keyword-match and the 3x3 grid, number every
# candidate box (OCR text boxes + OpenCV-detected UI boxes incl. text-less ones)
# and let the model pick one by number. Catches empty inputs / icon buttons.
MARKS_ENABLE = os.environ.get("MARKS_ENABLE", "true").lower() in ("1", "true", "yes")
MARKS_MAX = int(os.environ.get("MARKS_MAX", "40"))   # labels drawn (legibility cap)
# Inside the 3x3 descent, run OCR on the (small) region in parallel with the grid
# LLM once this many splits have been applied (>= 2 keeps OCR to small regions
# where its text reading is reliable).
OCR_PARALLEL_MIN_SPLITS = int(os.environ.get("OCR_PARALLEL_MIN_SPLITS", "2"))
# Keyword-vs-OCR-text similarity (0..1) at/above which a box counts as a match
# and is clicked directly, without asking the LLM.
OCR_MATCH_MIN = float(os.environ.get("OCR_MATCH_MIN", "0.8"))
# Hybrid grounding: a VERY confident OCR keyword match (>= this) is clicked BEFORE
# UI-TARS. A text-labelled target ("Aa", "Gửi") is read more precisely by OCR than
# a VLM points at it, so OCR wins first; UI-TARS handles the text-less/icon cases.
OCR_EXACT_MIN = float(os.environ.get("OCR_EXACT_MIN", "0.9"))


def _norm(s: str) -> str:
    """Lowercase, strip Vietnamese accents (đ->d), collapse spaces - so OCR
    misreads of diacritics still match ('Truong' == 'Trường')."""
    import unicodedata
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _sim(keyword: str, text: str) -> float:
    """Fuzzy similarity of a keyword to an OCR text box (0..1)."""
    from difflib import SequenceMatcher
    k, t = _norm(keyword), _norm(text)
    if not k or not t:
        return 0.0
    if k == t:
        return 1.0
    if len(k) >= 2 and k in t:            # keyword inside the box text
        return min(1.0, 0.85 + 0.15 * len(k) / len(t))
    if len(t) >= 3 and t in k:            # box holds part of the keyword
        return 0.7 * len(t) / len(k)
    best = SequenceMatcher(None, k, t).ratio()
    for w in t.split():                   # keyword vs each word in the box
        best = max(best, SequenceMatcher(None, k, w).ratio())
    return best


def _in_area(it: dict, area: str, w: int, h: int) -> bool:
    cx, cy = it["cx"], it["cy"]
    return {
        "left": cx < w * 0.45, "right": cx > w * 0.55,
        "top": cy < h * 0.45, "bottom": cy > h * 0.55,
        "center": w * 0.25 < cx < w * 0.75 and h * 0.25 < cy < h * 0.75,
    }.get(area, True)


def _keywords_from(target: str, keywords) -> list:
    """Model-supplied keywords + any quoted text in the target, deduped."""
    import re
    out = []
    if isinstance(keywords, str):
        keywords = [keywords]
    for k in keywords or []:
        if isinstance(k, str) and k.strip():
            out.append(k.strip())
    for q in re.findall(r"['\"“”‘’]([^'\"“”‘’]{1,40})['\"“”‘’]", target or ""):
        out.append(q.strip())
    seen, res = set(), []
    for k in out:
        n = _norm(k)
        if n and n not in seen:
            seen.add(n)
            res.append(k)
    return res


# Where the target sits relative to a visible text anchor (OCR-found).
_REL_VI = {
    "near": "gần", "below": "bên dưới", "above": "bên trên",
    "left_of": "bên trái", "right_of": "bên phải",
    "bottom_of": "ở phần dưới cùng của khung bên dưới",
}


def _relative_region(a: dict, relation: str, w: int, h: int):
    """Search box (x0,y0,x1,y1) in image space relative to anchor box `a`.
    Columns are widened sideways because an anchor (e.g. a chat-header name)
    is usually narrower than the panel it heads. None if degenerate."""
    aw, ah = a["x1"] - a["x0"], a["y1"] - a["y0"]
    padx, pady = max(220, 2 * aw), max(30, ah)
    if relation in ("below", "bottom_of"):
        x0, x1, y0, y1 = a["x0"] - padx, a["x1"] + padx, a["y1"], h
        if relation == "bottom_of":          # lowest ~40% of the panel below it
            y0 += int((y1 - y0) * 0.6)
    elif relation == "above":
        x0, x1, y0, y1 = a["x0"] - padx, a["x1"] + padx, 0, a["y0"]
    elif relation == "right_of":
        x0, x1, y0, y1 = a["x1"], w, a["y0"] - pady, a["y1"] + pady
    elif relation == "left_of":
        x0, x1, y0, y1 = 0, a["x0"], a["y0"] - pady, a["y1"] + pady
    else:                                    # near
        x0, x1, y0, y1 = a["x0"] - padx, a["x1"] + padx, a["y0"] - 150, a["y1"] + 150
    x0, y0 = max(0, int(x0)), max(0, int(y0))
    x1, y1 = min(w, int(x1)), min(h, int(y1))
    if x1 - x0 < 40 or y1 - y0 < 20:
        return None
    return x0, y0, x1, y1


def _in_region(it: dict, r) -> bool:
    return r is None or (r[0] <= it["cx"] <= r[2] and r[1] <= it["cy"] <= r[3])


# Words that mean the task includes an OUTWARD delivery step (send/post/comment),
# not just reading/searching. Used to stop the agent ending right after it has
# gathered info but before it has actually sent anything.
_DELIVER_INTENTS = ("gửi", "gởi", "nhắn", "nhắn tin", "trả lời", "đăng",
                    "bình luận", "comment", "reply", "soạn tin", "gửi tin")


def _needs_delivery(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in _DELIVER_INTENTS)


# Signals that the model DELIBERATELY stopped to ask the user or because it is
# blocked: target not found, ambiguous, or it wants confirmation first. When a
# stop looks like this we must NOT nag it to "send anyway" - that is exactly how
# a message gets fired at the wrong place (e.g. typing a greeting into whatever
# chat is open because "My Documents" is not a real contact). Surface the text.
_ASK_BLOCK_CUES = (
    "không tìm thấy", "không thấy", "không tồn tại", "không có cuộc trò chuyện",
    "không có liên hệ", "không phải là", "không phải một", "không phải người",
    "không phải liên hệ", "là một thư mục", "là thư mục", "không rõ", "chưa rõ",
    "không chắc", "bạn muốn", "bạn có muốn", "cho tôi biết", "cho mình biết",
    "vui lòng cho", "xin xác nhận", "xác nhận", "hãy xác nhận", "nền tảng nào",
    "người nào", "ai trong", "đúng người", "có phải", "bạn cần", "bạn định",
)


def _is_ask_or_blocked(content: str) -> bool:
    """True when the model's stopping text is a question to the user or says it
    is blocked (can't find the target / ambiguous / wants confirmation). Such a
    stop is legitimate and must be shown to the user, never nagged past."""
    t = (content or "").lower()
    if not t:
        return False
    if "?" in content:
        return True
    return any(c in t for c in _ASK_BLOCK_CUES)


# Sequencing / info words that signal a multi-step task worth planning first.
_SEQ_WORDS = ("rồi", "sau đó", "tiếp theo", "sau khi", "xong thì", "và sau", "kế tiếp")
_INFO_WORDS = ("tìm", "tra", "tổng hợp", "search", "đọc", "lướt", "tin tức",
               "thông tin", "giá", "kiểm tra")


def _needs_plan(text: str) -> bool:
    """A task worth decomposing first: has an ordering word, or gathers info AND
    then delivers it. Simple single actions / plain questions skip planning."""
    t = (text or "").lower()
    if any(w in t for w in _SEQ_WORDS):
        return True
    return _needs_delivery(text) and any(w in t for w in _INFO_WORDS)
# qwen3-vl is a "thinking" model: left on, it burns the whole generation on a
# reasoning monologue and never emits the tool call (blank turns, 80s/step).
# Off = respond directly with tool calls. Set OLLAMA_THINK=true to re-enable.
THINK = os.environ.get("OLLAMA_THINK", "false").lower() in ("1", "true", "yes")

BASE_PROMPT = (
    "Bạn là trợ lý kiêm tác tử điều khiển máy tính Windows từ xa qua các công cụ được cấp. "
    "TRƯỚC TIÊN phân loại yêu cầu:\n"
    "- Nếu chỉ là câu hỏi thường, trò chuyện, hỏi kiến thức, giải thích... KHÔNG cần "
    "nhìn/điều khiển máy: trả lời THẲNG bằng văn bản, KHÔNG gọi công cụ. Không từ chối "
    "chỉ vì 'không có màn hình'.\n"
    "- Nếu cần thông tin mới/không chắc mà không có sẵn trong kiến thức (giá cả, tin tức, "
    "số liệu cập nhật): gọi web_search(query) để tra Internet (chạy trên máy chủ, không "
    "mở trình duyệt). Kết quả có kèm nội dung vài trang đầu; nếu cần chi tiết hơn, gọi "
    "fetch_url(link) để đọc sâu một trang. Tổng hợp rồi trả lời tự nhiên, không cần kèm "
    "link trừ khi người dùng hỏi nguồn.\n"
    "- Nếu yêu cầu là thao tác trên máy (mở app, gõ, bấm, lướt Facebook...): dùng công cụ.\n"
    "QUAN TRỌNG - yêu cầu NHIỀU BƯỚC (vd 'tìm/tổng hợp X RỒI nhắn cho Y', 'đọc tin rồi "
    "đăng'): phải làm HẾT các phần. Khi đã có thông tin (từ web_search/đọc màn hình), "
    "ĐỪNG dừng lại và chỉ trả lời chữ — hãy DÙNG thông tin đó để làm tiếp phần hành động "
    "(mở khung chat, gõ nội dung, gửi...). Chỉ trả lời bằng văn bản (kết thúc) khi TẤT CẢ "
    "các phần đã xong.\n"
    "Khi thao tác trên máy, trả lời bằng cách GỌI MỘT CÔNG CỤ (screenshot/tap/move/click/"
    "type/key/hotkey/scroll), suy nghĩ thật ngắn gọn. Để bấm phần tử trên màn hình, "
    "ưu tiên tap(target, keywords, area) thay vì click toạ độ; keywords là các chữ "
    "nhìn thấy trên/cạnh phần tử (tên người, nhãn nút, chữ gợi ý). Nếu mục tiêu không có "
    "chữ, thêm anchor (chữ của phần tử mốc gần đó) + relation (below/above/left_of/"
    "right_of/bottom_of). Không bịa toạ độ: gọi "
    "screenshot để nhìn, ảnh đính ở lượt kế, lấy toạ độ trên ảnh đó. Sau hành động "
    "quan trọng, screenshot để xác minh. Chỉ chốt kết quả từ những gì THẤY trên ảnh. "
    "Nếu nhiệm vụ cần xem nhiều hơn một màn hình (lướt/tổng hợp nhiều mục): phải "
    "scroll rồi screenshot lại và lặp cho tới khi gom đủ; KHÔNG kết luận hay tóm tắt "
    "từ một ảnh duy nhất. Khi xong hoặc không thể tiếp tục, trả lời "
    "bằng văn bản ngắn, không gọi thêm công cụ. /no_think"
)

# Skills live in groups: skills/<group>/<name>/SKILL.md, keyed "group/name".
#   common/*   - how to SEE and LOCATE (screenshot, grid, OCR, anchors). No task
#                procedures. Loaded for every task.
#   windows/*  - general PC tasks, loaded on a keyword hit.
#   facebook/* - facebook.com (Chrome, Vietnamese UI). The group switches on only
#                when the request is about Facebook; then facebook/overview (the
#                page layout map) loads plus the sub-skills whose keywords match.
# Keeping the prompt to what the request needs matters: an 8B model drowns in a
# 10k-char prompt. Keyword match is on the lowercased request.
SKILL_GROUPS = ("common", "windows", "facebook", "zalo")

# Only Facebook-DISTINCTIVE words gate the group. Generic chat words ("tin nhắn",
# "nhắn tin", "khung chat") are deliberately NOT here: a platform-less chat request
# loads no app group, so common/context decides from the foreground app (or asks)
# instead of defaulting to Facebook. Feed/post/react words stay (Zalo has no such
# skills). Naming an app by keyword still turns its group on.
FACEBOOK_TRIGGERS = ["facebook", "fb", "messenger", "bảng tin", "bản tin",
                     "newsfeed", "newfeed",
                     "bạn bè", "kết bạn",
                     "bình luận", "thả tim", "cảm xúc", "đăng bài", "đăng trạng thái",
                     "trang cá nhân"]
# Zalo switches on only when the app is named explicitly ("zalo"). Many chat words
# ("tin nhắn", "nhắn") also trigger Facebook, so naming Zalo SUPPRESSES Facebook
# (see select_skills) to avoid loading two chat apps' skills at once.
ZALO_TRIGGERS = ["zalo"]

SKILL_KEYWORDS = {
    # None = always load
    "common/screen-reading": None,
    "common/locate":         None,
    # Figure out which app is in the foreground when the request names no platform
    # (e.g. "gửi tin nhắn cho X") instead of defaulting blindly.
    "common/context":        None,

    "windows/open-app":     ["mở", "file", "thư mục", "folder", "ứng dụng", "app",
                             "notepad", "chạy", "run", "explorer"],
    "windows/hotkeys":      ["phím", "hotkey", "ctrl", "copy", "paste", "dán", "sao chép",
                             "lưu", "save", "alt", "tab"],
    "windows/text-editing": ["gõ", "nhập", "soạn", "văn bản", "type", "sửa"],
    "windows/web-browser":  ["web", "trình duyệt", "chrome", "edge", "browser", "trang web",
                             "url", "google", "truy cập", "website"],
    "windows/web-forms":    ["biểu mẫu", "form", "điền", "đăng ký", "submit", "đăng kí"],

    # facebook/* only considered when FACEBOOK_TRIGGERS hit (see select_skills)
    "facebook/overview":       None,   # always, once the group is on
    "facebook/open-chat":      ["mở khung chat", "mở chat", "nhắn", "tin nhắn", "chat",
                                "messenger", "gửi lời", "gửi tin"],
    "facebook/reply-message":  ["trả lời", "nhắn", "tin nhắn", "gửi lời", "gửi tin",
                                "reply"],
    "facebook/browse-feed":    ["lướt", "bảng tin", "newsfeed", "newfeed", "feed",
                                "tóm tắt", "đọc tin", "có gì mới", "cuộn", "bản tin",
                                "tin tức"],
    "facebook/search-friends": ["tìm", "search", "bạn bè", "trang cá nhân", "profile",
                                "kết bạn"],
    "facebook/post-status":    ["đăng bài", "đăng trạng thái", "đăng status", "status",
                                "bạn đang nghĩ gì", "chia sẻ cảm nghĩ"],
    "facebook/comment-react":  ["bình luận", "comment", "thích", "like", "cảm xúc",
                                "thả tim", "react"],
    "facebook/notifications":  ["thông báo", "notification", "chuông"],

    # zalo/* only considered when ZALO_TRIGGERS hit (see select_skills)
    "zalo/overview":      None,   # always, once the group is on
    # Foundational: every Zalo action (send/read) first needs a conversation open,
    # so open-chat always loads with the group (acts as send/read's dependency).
    "zalo/open-chat":     None,
    "zalo/send-message":  ["gửi", "nhắn", "tin nhắn", "trả lời", "gửi lời", "gửi tin",
                           "reply", "soạn", "chúc", "nhắn tin"],
    "zalo/read-chat":     ["đọc", "xem tin", "tin nhắn mới", "tóm tắt", "có gì",
                           "chưa đọc", "đọc tin", "nội dung"],
}


def _load_all_skills(path: str = "skills") -> dict:
    """Read skills/<group>/<name>/SKILL.md for the known groups only (any other
    folder under skills/, e.g. an old copy, is ignored)."""
    out = {}
    for group in SKILL_GROUPS:
        for f in sorted(glob.glob(os.path.join(path, group, "*", "SKILL.md"))):
            name = f"{group}/{os.path.basename(os.path.dirname(f))}"
            try:
                with open(f, encoding="utf-8") as fh:
                    out[name] = fh.read().strip()
            except OSError:
                pass
    return out


ALL_SKILLS = _load_all_skills()


def select_skills(text: str) -> tuple[str, list]:
    """Pick common + keyword-matched skills for this request. Returns (prompt, names).
    App groups (facebook/zalo) are gated: a group's skills load only when that app
    is in scope. Naming Zalo explicitly suppresses Facebook so the two chat apps'
    skills never load together (Facebook also triggers on generic chat words)."""
    low = f" {text.lower()} "
    zalo_on = any(k in low for k in ZALO_TRIGGERS)
    facebook_on = (not zalo_on) and any(k in low for k in FACEBOOK_TRIGGERS)
    active = {"facebook": facebook_on, "zalo": zalo_on}   # gated groups
    chosen = []
    for name, kws in SKILL_KEYWORDS.items():
        if name not in ALL_SKILLS:
            continue
        group = name.split("/", 1)[0]
        if group in active and not active[group]:
            continue
        if kws is None or any(k in low for k in kws):
            chosen.append(name)
    if not chosen:
        return "", []
    body = "\n\n---\n\n".join(ALL_SKILLS[n] for n in chosen)
    return "\n\n# Kỹ năng (quy trình tham khảo)\n\n" + body, chosen


def build_system_prompt(text: str) -> tuple[str, list]:
    skills, names = select_skills(text)
    return BASE_PROMPT + skills, names

ollama_client = ollama.AsyncClient(
    host=os.environ.get("OLLAMA_HOST", "http://localhost:11434")
)


DEBUG = os.environ.get("AGENT_DEBUG", "false").lower() in ("1", "true", "yes")
# Mirror task logs to the browser chat panel (via relay "log" messages).
UI_LOGS = os.environ.get("UI_LOGS", "true").lower() in ("1", "true", "yes")
CACHE_ENABLED = os.environ.get("TASK_CACHE_ENABLED", "true").lower() in (
    "1", "true", "yes"
)
CACHE_DB = os.environ.get(
    "TASK_CACHE_DB", str(Path(__file__).with_name("agent_cache.sqlite3"))
)
CACHE_THRESHOLD = float(os.environ.get("TASK_CACHE_THRESHOLD", "0.82"))
CACHE_MAX_AGE_DAYS = int(os.environ.get("TASK_CACHE_MAX_AGE_DAYS", "30"))
SCREEN_CACHE_TTL = float(os.environ.get("SCREEN_CACHE_TTL_SECONDS", "2.0"))
OCR_CACHE_TTL = float(os.environ.get("OCR_CACHE_TTL_SECONDS", "2.0"))
SCREEN_MUTATING_TOOLS = frozenset({
    "move",
    "click",
    "tap",
    "scroll",
    "type",
    "key",
    "hotkey",
})

# Set by TaskRunner once the event loop is up; log() best-effort enqueues lines
# for the drain coroutine to forward to the UI. CURRENT_TASK tags each line.
_log_q = None
CURRENT_TASK = ""


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    q = _log_q
    if q is not None:
        try:
            q.put_nowait(msg)
        except Exception:  # noqa: BLE001 - queue full / not ready: drop the line
            pass


def annotate_grid(jpeg: bytes, step: int = 100):
    """Draw a labelled coordinate grid over the screenshot so the vision model
    can READ pixel coordinates off the gridlines instead of guessing. Returns
    (png_bytes, (w, h)). This image goes only to the local Ollama, not the relay,
    so size is unconstrained. Returns the original bytes if Pillow is missing."""
    try:
        from io import BytesIO
        from PIL import Image, ImageDraw
    except Exception:  # noqa: BLE001
        return jpeg, (0, 0)
    im = Image.open(BytesIO(jpeg)).convert("RGB")
    w, h = im.size
    d = ImageDraw.Draw(im)
    red = (255, 40, 40)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=red, width=1)
        if x:
            d.text((x + 1, 1), str(x), fill=red)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=red, width=1)
        if y:
            d.text((1, y + 1), str(y), fill=red)
    out = BytesIO()
    im.save(out, format="PNG")
    return out.getvalue(), (w, h)


def clip(s: str, n: int = 400) -> str:
    """Full text in debug mode, else truncated with a length marker."""
    s = s or ""
    if DEBUG or len(s) <= n:
        return s
    return s[:n] + f" …(+{len(s) - n} chars)"


def dump_messages(task_id: str, messages: list):
    """Verbose: list the conversation sent to the LLM (image bytes stripped)."""
    if not DEBUG:
        return
    for i, mm in enumerate(messages):
        get = mm.get if isinstance(mm, dict) else (lambda k, d=None: getattr(mm, k, d))
        role = get("role", "?")
        content = get("content", "") or ""
        has_img = bool(get("images", None))
        log(f"   msg[{i}] {role} len={len(content)}{' +img' if has_img else ''}: {clip(content, 160)}")


class TaskRunner:
    """Owns one active task at a time (a home single-user setup)."""

    def __init__(self, relay: RelayClient):
        self.relay = relay
        self.task_cache = TaskCache(
            CACHE_DB,
            enabled=CACHE_ENABLED,
            threshold=CACHE_THRESHOLD,
            max_age_days=CACHE_MAX_AGE_DAYS,
        )
        self._cancel = asyncio.Event()
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
            global _log_q
            _log_q = asyncio.Queue(maxsize=1000)
            asyncio.create_task(self._drain_logs())

    async def _drain_logs(self):
        """Forward buffered log lines to the browser as relay 'log' messages.
        Best-effort: dropped silently when the relay is down."""
        while True:
            line = await _log_q.get()
            try:
                await self.relay.send_ui({"type": "log", "task_id": CURRENT_TASK,
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
        self._cancel.clear()
        self._active = asyncio.create_task(self.run_task(task_id, text))

    async def on_cancel(self, msg: dict):
        self._cancel.set()
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

    @staticmethod
    def _safe_tag(tag: str) -> str:
        """ASCII-only filename fragment: strip Vietnamese accents, keep [A-Za-z0-9_-]."""
        import unicodedata
        t = unicodedata.normalize("NFKD", tag).encode("ascii", "ignore").decode()
        t = "".join(c if (c.isalnum() or c in "_-") else "_" for c in t).strip("_")
        return t[:24] or "img"

    def _save(self, task_id: str, tag: str, data: bytes, ext: str = "png"):
        if not SAVE_IMAGES or not data:
            return
        try:
            os.makedirs(IMAGE_DIR, exist_ok=True)
            fn = os.path.join(IMAGE_DIR,
                              f"{time.strftime('%H%M%S')}_{task_id}_{self._safe_tag(tag)}.{ext}")
            with open(fn, "wb") as f:
                f.write(data)
            log(f"[{task_id}] saved {fn}")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] save image failed: {e}")

    def _mark_click(self, task_id: str, ix: int, iy: int, tag: str):
        """Save the raw screenshot with a marker at the clicked image point, so
        it is obvious whether the click landed on the intended element."""
        if not SAVE_IMAGES or not self._raw_frame:
            return
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB")
            d = ImageDraw.Draw(im)
            r = 14
            d.ellipse([ix - r, iy - r, ix + r, iy + r], outline=(0, 255, 0), width=3)
            d.line([ix - r, iy, ix + r, iy], fill=(0, 255, 0), width=2)
            d.line([ix, iy - r, ix, iy + r], fill=(0, 255, 0), width=2)
            out = BytesIO()
            im.save(out, format="PNG")
            self._save(task_id, tag, out.getvalue())
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] mark_click failed: {e}")

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
        deadline = time.monotonic() + TASK_TIMEOUT
        global CURRENT_TASK
        CURRENT_TASK = task_id
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
        needs_deliver = _needs_delivery(text)

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
            if self._cancel.is_set():
                log(f"[{task_id}] cancelled at step {step}")
                await self.relay.send_ui({"type": "done", "task_id": task_id,
                                          "result": "đã huỷ"})
                return
            if time.monotonic() > deadline:
                log(f"[{task_id}] task timeout at step {step}")
                await self.relay.send_ui({"type": "done", "task_id": task_id,
                                          "result": "hết thời gian"})
                return
            try:
                has_img = any("images" in mm for mm in messages if isinstance(mm, dict))
                log(f"[{task_id}] step {step}: calling LLM ({MODEL}, {len(messages)} msgs"
                    f"{', +image' if has_img else ''}, think={THINK}) ...")
                dump_messages(task_id, messages)
                t0 = time.monotonic()
                resp = await asyncio.wait_for(
                    ollama_client.chat(model=MODEL, messages=messages,
                                       tools=TOOLS, think=THINK,
                                       options={"num_ctx": NUM_CTX}),
                    timeout=LLM_TIMEOUT)
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
                            "key enter để gửi." + NT})
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
                result = await self._run_tool(task_id, name, args)
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

        last = "error: không chạy được"
        for _attempt in range(1, MAX_RETRIES + 2):
            if self._cancel.is_set():
                return "error: đã huỷ"
            try:
                res = await self.relay.call_device(name, args, task_id, timeout=DEVICE_TIMEOUT)
            except asyncio.TimeoutError:
                last = "error: thiết bị không phản hồi (timeout)"
                continue
            except Exception as e:  # noqa: BLE001
                last = f"error: {e}"
                continue
            if res.get("ok"):
                detail = (res.get("result") or {}).get("detail", "")
                if name in SCREEN_MUTATING_TOOLS:
                    self._invalidate_screen_cache()
                return f"ok {detail}".strip()
            last = f"error: {res.get('error', 'thất bại')}"
        return last

    async def _locate(self, task_id: str, target: str, kws: list = None, area: str = "any",
                      region=None, ctx: str = ""):
        """3x3 region descent (quad-tree style): recursively split the screenshot
        into 9 numbered cells and ask the model which cell holds `target`, zooming
        into it each level. The model only picks a digit 1-9 (far easier than
        reading pixels), so it stays accurate even on a weak VLM. Returns an
        (x, y) point in original image space, or None if not found."""
        if not (self._raw_frame and self._img_w):
            return None
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw, ImageFont
        except Exception:  # noqa: BLE001
            return None
        try:
            base = Image.open(BytesIO(self._raw_frame)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] locate open failed: {e}")
            return None

        W, H = float(self._img_w), float(self._img_h)
        if region:   # start inside the anchor-relative search box
            x0, y0 = float(region[0]), float(region[1])
            w, h = float(region[2] - region[0]), float(region[3] - region[1])
        else:
            x0, y0, w, h = 0.0, 0.0, W, H
        # Split until a cell is ~50 px (max 3 levels; fewer for a small region).
        levels = 1
        while levels < 3 and max(w, h) / (3 ** levels) > 50:
            levels += 1
        for level in range(levels):
            region = base.crop((int(x0), int(y0), int(x0 + w), int(y0 + h)))
            scale = max(1, int(900 / max(w, 1)))
            rw, rh = max(1, int(w * scale)), max(1, int(h * scale))
            im = region.resize((rw, rh))
            d = ImageDraw.Draw(im)
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", max(20, rh // 7))
            except Exception:  # noqa: BLE001
                font = ImageFont.load_default()
            for i in (1, 2):
                d.line([(rw * i // 3, 0), (rw * i // 3, rh)], fill=(255, 40, 40), width=2)
                d.line([(0, rh * i // 3), (rw, rh * i // 3)], fill=(255, 40, 40), width=2)
            for n in range(9):
                cx = (n % 3) * rw // 3 + rw // 6
                cy = (n // 3) * rh // 3 + rh // 6
                d.text((cx, cy), str(n + 1), fill=(255, 255, 0), font=font,
                       anchor="mm", stroke_width=3, stroke_fill=(0, 0, 0))
            out = BytesIO()
            im.save(out, format="PNG")
            self._save(task_id, f"locate_L{level}", out.getvalue())
            hint = f" (chữ trên/cạnh nó: {', '.join(kws)})" if kws else ""
            if level == 0 and ctx:
                hint += f" {ctx}"
            elif level == 0 and area and area != "any":
                hint += f" (thường ở vùng {area} màn hình)"
            note = (f"Ảnh được chia 9 ô, đánh số 1-9 (màu vàng, theo thứ tự trái→phải, "
                    f"trên→dưới). Phần tử '{target}'{hint} nằm ở ô số nào? Chỉ trả về MỘT chữ "
                    "số 1-9. Nếu hoàn toàn không thấy, trả 0. /no_think")

            # Once the region is small (>= OCR_PARALLEL_MIN_SPLITS splits done),
            # OCR it in parallel with the grid LLM call (OCR on CPU, LLM on GPU).
            # OCR is fast and gives an exact point; on a match, cancel the LLM and
            # finish here.
            if OCR_ENABLE and kws and level >= OCR_PARALLEL_MIN_SPLITS:
                ocr_task = asyncio.create_task(self._ocr_region(task_id, x0, y0, w, h, kws))
                llm_task = asyncio.create_task(self._grid_pick(task_id, level, out.getvalue(), note))
                ocr_pt = await ocr_task
                if ocr_pt:
                    llm_task.cancel()
                    try:
                        await llm_task
                    except asyncio.CancelledError:
                        pass
                    log(f"[{task_id}] locate L{level}: OCR song song trúng -> dừng")
                    return ocr_pt
                cell = await llm_task
            else:
                cell = await self._grid_pick(task_id, level, out.getvalue(), note)
            if cell is None:
                return None
            log(f"[{task_id}] locate L{level}: '{target}' -> cell {cell} "
                f"(region {int(x0)},{int(y0)} {int(w)}x{int(h)})")
            if cell == 0:
                return None
            col, row = (cell - 1) % 3, (cell - 1) // 3
            x0 += col * w / 3
            y0 += row * h / 3
            w /= 3
            h /= 3
        return int(x0 + w / 2), int(y0 + h / 2)

    async def _ocr_items(self, task_id: str):
        """Return OCR boxes, reusing results for the same fresh frame."""
        if not self._raw_frame or not self._frame_hash:
            return None
        now = time.monotonic()
        if (
            self._ocr_cache_items is not None
            and self._ocr_cache_hash == self._frame_hash
            and now - self._ocr_cache_at <= OCR_CACHE_TTL
        ):
            log(f"[{task_id}] OCR cache hit: {len(self._ocr_cache_items)} vùng chữ")
            return self._ocr_cache_items

        loop = asyncio.get_running_loop()
        try:
            items = await loop.run_in_executor(None, ocr.run_ocr, self._raw_frame, OCR_LANG)
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] OCR error: {e}")
            return None
        if items is None:
            log(f"[{task_id}] OCR unavailable (chưa cài paddleocr?) -> fallback")
            return None
        self._ocr_cache_hash = self._frame_hash
        self._ocr_cache_at = time.monotonic()
        self._ocr_cache_items = items
        log(f"[{task_id}] OCR: {len(items)} vùng chữ")
        return items

    def _find_anchor(self, task_id: str, items: list, anchor_kws: list, area: str,
                     relation: str):
        """OCR boxes matching the anchor keywords (>= OCR_MATCH_MIN), best first.
        Returns a LIST of candidate boxes (the caller disambiguates ambiguous
        anchors with the target keywords). Order: in-area preferred, then score;
        for below/bottom_of a header name is assumed to sit above its panel, so
        among equally-scored boxes the top-most comes first."""
        W, H = self._img_w, self._img_h
        hits = []
        for it in items:
            s = max((_sim(k, it["text"]) for k in anchor_kws), default=0.0)
            if s >= OCR_MATCH_MIN:
                hits.append((s, it))
        if not hits:
            log(f"[{task_id}] anchor {anchor_kws}: không thấy trên OCR")
            return []
        use_area = area if area and area != "any" else ""
        in_area = [x for x in hits if use_area and _in_area(x[1], use_area, W, H)]
        hits = in_area or hits
        # Primary sort by score desc; tie-break top-most for below/bottom_of.
        hits.sort(key=lambda x: (-x[0], x[1]["cy"] if relation in ("below", "bottom_of") else 0))
        cands = [it for _s, it in hits]
        a = cands[0]
        log(f"[{task_id}] anchor '{a['text']}' ({hits[0][0]:.2f}) @({a['cx']},{a['cy']})"
            f"{' — ' + str(len(cands)) + ' ứng viên' if len(cands) > 1 else ''}")
        return cands

    def _save_region(self, task_id: str, a: dict, region):
        """Debug image: anchor box (blue) + search region (green)."""
        if not SAVE_IMAGES or not self._raw_frame:
            return
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB")
            d = ImageDraw.Draw(im)
            d.rectangle([a["x0"], a["y0"], a["x1"], a["y1"]], outline=(40, 120, 255), width=3)
            if region:
                d.rectangle(list(region), outline=(0, 220, 0), width=3)
            out = BytesIO()
            im.save(out, format="PNG")
            self._save(task_id, "tap_region", out.getvalue())
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] save region failed: {e}")

    def _best_match(self, scored, region, strong_only=True, thr=None):
        """From [(score, box)], take matches >= `thr` (default OCR_MATCH_MIN); among
        the top (within 0.05) pick the box nearest the region/screen centre. Returns
        (x,y) or None."""
        W, H = self._img_w, self._img_h
        th = thr if thr is not None else OCR_MATCH_MIN
        strong = [(s, it) for s, it in scored if s >= th]
        if not strong:
            return None
        top = strong[0][0] if scored and scored[0][0] == strong[0][0] else max(s for s, _ in strong)
        best = [it for s, it in strong if top - s < 0.05]
        if len(best) > 1:
            if region:
                rx, ry = (region[0] + region[2]) / 2, (region[1] + region[3]) / 2
            else:
                rx, ry = W / 2, H / 2
            best.sort(key=lambda it: (it["cx"] - rx) ** 2 + (it["cy"] - ry) ** 2)
        it = best[0]
        return it["cx"], it["cy"]

    async def _tap_ocr(self, task_id: str, target: str, kws: list, area: str,
                       items: list, region=None, thr=None):
        """Direct OCR keyword match over the boxes inside `region` (whole screen
        if None). No LLM, no cap. Accent-insensitive fuzzy match; a hit >= `thr`
        (default OCR_MATCH_MIN) is clicked directly. Returns (x,y) image space, or
        None to fall back."""
        if not kws:
            return None                      # nothing to match text against
        items = [it for it in items if _in_region(it, region)]
        if not items:
            log(f"[{task_id}] OCR: không có chữ trong vùng tìm -> fallback")
            return None
        W, H = self._img_w, self._img_h
        use_area = area if area and area != "any" else ""
        scored = [(max((_sim(k, it["text"]) for k in kws), default=0.0), it) for it in items]
        if use_area:
            scored = [x for x in scored if _in_area(x[1], use_area, W, H)] or scored
        scored.sort(key=lambda x: x[0], reverse=True)
        log(f"[{task_id}] OCR match: {len(items)} vùng {'trong vùng tìm' if region else 'toàn màn'}, "
            f"keywords={kws}; top: "
            + ", ".join(f"'{it['text']}'={s:.2f}" for s, it in scored[:5]))
        th = thr if thr is not None else OCR_MATCH_MIN
        pt = self._best_match(scored, region, thr=th)
        if pt:
            log(f"[{task_id}] OCR match @({pt[0]},{pt[1]}) (>= {th}) — không cần LLM")
        else:
            log(f"[{task_id}] OCR: không vùng nào khớp >= {th} -> fallback")
        return pt

    async def _ocr_region(self, task_id: str, x0, y0, w, h, kws: list):
        """OCR just the cropped region (upscaled for small text), match keywords,
        map the hit back to image space. For the parallel short-circuit inside
        the grid descent. Returns (x,y) or None."""
        if not kws or not self._raw_frame:
            return None
        try:
            from io import BytesIO
            from PIL import Image
            base = Image.open(BytesIO(self._raw_frame)).convert("RGB")
        except Exception:  # noqa: BLE001
            return None
        x0i, y0i = int(x0), int(y0)
        x1i, y1i = int(x0 + w), int(y0 + h)
        if x1i - x0i < 8 or y1i - y0i < 8:
            return None
        Z = max(1, int(1100 / max(w, 1)))    # upscale a small crop so OCR reads it
        try:
            crop = base.crop((x0i, y0i, x1i, y1i))
            if Z > 1:
                crop = crop.resize((int((x1i - x0i) * Z), int((y1i - y0i) * Z)))
            buf = BytesIO()
            crop.save(buf, format="PNG")
        except Exception:  # noqa: BLE001
            return None
        loop = asyncio.get_running_loop()
        try:
            items = await loop.run_in_executor(None, ocr.run_ocr, buf.getvalue(), OCR_LANG)
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] region OCR error: {e}")
            return None
        if not items:
            return None
        # Map crop coords back to image space, then score.
        scored = []
        for it in items:
            m = dict(it)
            m["cx"] = x0i + int(it["cx"] / Z)
            m["cy"] = y0i + int(it["cy"] / Z)
            s = max((_sim(k, it["text"]) for k in kws), default=0.0)
            scored.append((s, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        pt = self._best_match(scored, (x0i, y0i, x1i, y1i))
        if pt:
            log(f"[{task_id}] region OCR match '{scored[0][1]['text']}' -> img({pt[0]},{pt[1]})")
        return pt

    async def _tap_marks(self, task_id: str, target: str, kws: list, area: str,
                         items, region=None):
        """Set-of-Mark locate (A+B): number the candidate boxes - OCR text boxes
        AND OpenCV-detected UI boxes (incl. text-less inputs/buttons) - inside the
        search area, draw them on a region-cropped, upscaled image, and let the
        model pick one by number. Returns (x,y) image space, or None to fall back
        to the 3x3 grid."""
        if not (MARKS_ENABLE and self._raw_frame and self._img_w):
            return None
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw, ImageFont
        except Exception:  # noqa: BLE001
            return None
        W, H = self._img_w, self._img_h
        use_area = area if area and area != "any" else ""

        def _ok(b):
            return _in_region(b, region) and (not use_area or _in_area(b, use_area, W, H))

        cand = []
        for it in (items or []):
            if _ok(it):
                cand.append({"x0": it["x0"], "y0": it["y0"], "x1": it["x1"], "y1": it["y1"],
                             "cx": it["cx"], "cy": it["cy"], "label": (it.get("text") or "")[:22]})
        try:
            boxes = await asyncio.to_thread(ui_boxes.detect_boxes, self._raw_frame)
        except Exception:  # noqa: BLE001
            boxes = []
        for b in boxes:                       # text-less controls, skip OCR overlaps
            if _ok(b) and not any(ui_boxes._iou(b, c) > 0.6 for c in cand):
                cand.append({**b, "label": ""})
        if len(cand) < 2:
            return None

        rx, ry = ((region[0] + region[2]) / 2, (region[1] + region[3]) / 2) if region else (W / 2, H / 2)
        cand.sort(key=lambda c: (c["cx"] - rx) ** 2 + (c["cy"] - ry) ** 2)
        cand = cand[:MARKS_MAX]

        rx0, ry0, rx1, ry1 = map(int, region) if region else (0, 0, W, H)
        rx0, ry0 = max(0, rx0), max(0, ry0)
        rx1, ry1 = min(W, rx1), min(H, ry1)
        rw, rh = max(1, rx1 - rx0), max(1, ry1 - ry0)
        Z = max(1, min(3, int(1000 / rw)))
        try:
            base = Image.open(BytesIO(self._raw_frame)).convert("RGB")
            crop = base.crop((rx0, ry0, rx1, ry1))
            if Z > 1:
                crop = crop.resize((rw * Z, rh * Z))
            d = ImageDraw.Draw(crop)
            try:
                font = ImageFont.truetype("DejaVuSans-Bold.ttf", 15)
            except Exception:  # noqa: BLE001
                font = ImageFont.load_default()
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] marks draw failed: {e}")
            return None
        for i, c in enumerate(cand, 1):
            x0, y0 = (c["x0"] - rx0) * Z, (c["y0"] - ry0) * Z
            x1, y1 = (c["x1"] - rx0) * Z, (c["y1"] - ry0) * Z
            d.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=2)
            tag = str(i)
            d.rectangle([x0, y0, x0 + 9 * len(tag) + 6, y0 + 17], fill=(255, 0, 0))
            d.text((x0 + 3, y0 + 1), tag, fill=(255, 255, 255), font=font)
        buf = BytesIO()
        crop.save(buf, format="PNG")
        self._save(task_id, "marks", buf.getvalue())

        listing = "; ".join(f"{i}:{c['label'] or 'ô'}" for i, c in enumerate(cand, 1))
        note = (f"Ảnh có các VÙNG BẤM ĐƯỢC đánh số (khung đỏ). Cần bấm vào: '{target}'. "
                f"Chữ gợi ý gần phần tử: {kws}. Số:nhãn = {listing}. "
                f"Phần tử cần bấm ở số mấy? (ô nhập trống thường KHÔNG có chữ). "
                f"Chỉ trả về MỘT con số, 0 nếu không có. /no_think")
        n = await self._marks_pick(task_id, buf.getvalue(), note)
        if n and 1 <= n <= len(cand):
            c = cand[n - 1]
            log(f"[{task_id}] marks: {len(cand)} vùng -> chọn #{n} "
                f"'{c['label'] or 'ô trống'}' @({c['cx']},{c['cy']})")
            return c["cx"], c["cy"]
        log(f"[{task_id}] marks: {len(cand)} vùng, model không chọn (n={n}) -> fallback 3x3")
        return None

    async def _marks_pick(self, task_id: str, img_bytes: bytes, note: str):
        """One Set-of-Mark LLM call: returns the chosen number (int) or None."""
        try:
            resp = await ollama_client.chat(
                model=MODEL, think=THINK, options={"num_ctx": NUM_CTX},
                messages=[{"role": "user", "content": note, "images": [img_bytes]}])
            txt = (resp["message"].get("content") or "")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] marks LLM failed: {e}")
            return None
        import re
        mm = re.search(r"\d{1,3}", txt)
        if not mm:
            log(f"[{task_id}] marks: no number in {txt.strip()[:40]!r}")
            return None
        return int(mm.group(0))

    async def _grid_pick(self, task_id: str, level: int, img_bytes: bytes, note: str):
        """One grid LLM call: returns the chosen cell 1-9, 0 (not found), or None
        (error/no digit). Separate coroutine so it can run concurrently with OCR
        and be cancelled when OCR wins."""
        try:
            resp = await ollama_client.chat(
                model=MODEL, think=THINK, options={"num_ctx": NUM_CTX},
                messages=[{"role": "user", "content": note, "images": [img_bytes]}])
            txt = (resp["message"].get("content") or "")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] locate L{level} LLM failed: {e}")
            return None
        import re
        mm = re.search(r"[0-9]", txt)
        if not mm:
            log(f"[{task_id}] locate L{level}: no digit in {txt.strip()[:40]!r}")
            return None
        return int(mm.group(0))

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
            resp = await asyncio.wait_for(
                ollama_client.chat(model=MODEL, think=False,
                                   options={"num_ctx": NUM_CTX},
                                   messages=[{"role": "user", "content": prompt + " /no_think"}]),
                timeout=LLM_TIMEOUT)
            plan = (resp["message"].get("content") or "").strip()
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] plan: lỗi ({e}) -> chạy không kế hoạch")
            return None
        return plan or None

    async def _uia_find(self, task_id: str, kws: list, area: str = "any",
                        control: str = "any"):
        """(C) Ask the PC's UI Automation for a control; returns (sx,sy) in SCREEN
        pixels, or None. The query the PC gets is "<ctype> <area> <name>": the
        model may pin a ControlType (e.g. edit) and/or a Name (keyword). Tries each
        keyword; if a control type is given it also tries a name-less query (any
        Edit in the area = the chat input, whose Name is dynamic). A hit outside
        the requested area / off-screen is rejected. Transport error -> fall back."""
        if not UIA_ENABLE:
            return None
        import re
        names = [k.strip() for k in (kws or []) if len((k or '').strip()) >= 2]
        if control != "any":
            names.append("")            # name-less: match any control of that type
        if not names:
            log(f"[{task_id}] uia: bỏ qua (không có keywords/control)")
            return None
        log(f"[{task_id}] uia: hỏi PC control={control} area={area} name={names} ...")
        use_area = area if area and area != "any" else ""
        for nm in names:
            query = f"{control} {area} {nm}".strip()
            try:
                res = await self.relay.call_device("uia", {"query": query}, task_id,
                                                   timeout=UIA_TIMEOUT)
            except asyncio.TimeoutError:
                log(f"[{task_id}] uia '{query}': timeout -> bỏ UIA, dùng vision")
                return None
            except Exception as e:  # noqa: BLE001
                log(f"[{task_id}] uia '{query}': {e} -> bỏ UIA, dùng vision")
                return None
            detail = (res.get("result") or {}).get("detail", "") or ""
            if not res.get("ok"):
                # e.g. ESP chưa flash lệnh 'uia' -> "tool not allowed"
                log(f"[{task_id}] uia '{query}': lỗi thiết bị ({res.get('error', detail)}) "
                    "-> bỏ UIA, dùng vision")
                return None
            m = re.match(r"\s*(\d+)\s*,\s*(\d+)", detail)
            if m:
                sx, sy = int(m.group(1)), int(m.group(2))
                if self._scr_w and not (0 <= sx <= self._scr_w and 0 <= sy <= self._scr_h):
                    log(f"[{task_id}] uia '{query}' @({sx},{sy}) ngoài màn hình -> bỏ")
                    continue
                if use_area and self._scr_w and not _in_area({"cx": sx, "cy": sy},
                                                             use_area, self._scr_w, self._scr_h):
                    log(f"[{task_id}] uia '{query}' @({sx},{sy}) NGOÀI area '{area}' -> bỏ")
                    continue
                log(f"[{task_id}] uia '{query}' -> screen({sx},{sy})")
                return sx, sy
            log(f"[{task_id}] uia '{query}': {detail or 'none'}")
        log(f"[{task_id}] uia: không control hợp lệ -> dùng vision")
        return None

    async def _uia_marks(self, task_id: str, target: str, area: str = "any"):
        """UIA Set-of-Mark: PC numbers every interactive control on a screenshot
        (exact boxes from the a11y tree), uploads the annotated image, keeps the
        number->coords map. The LLM picks a number; the PC resolves it to real
        screen coords. Returns (sx,sy) in SCREEN pixels, or None to fall back."""
        if not (UIA_MARKS_ENABLE and self._scr_w):
            return None
        waiter = self.relay.arm_frame()
        try:
            res = await self.relay.call_device("uiamark", {"area": area}, task_id,
                                               timeout=max(UIA_TIMEOUT, 10))
        except asyncio.TimeoutError:
            log(f"[{task_id}] uiamark: timeout -> bỏ, dùng vision")
            return None
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] uiamark: {e} -> bỏ, dùng vision")
            return None
        detail = (res.get("result") or {}).get("detail", "") or ""
        if not res.get("ok"):
            log(f"[{task_id}] uiamark: lỗi thiết bị ({res.get('error', detail)}) -> vision")
            return None
        import re
        mm = re.search(r"(\d+)", detail)
        count = int(mm.group(1)) if mm else 0
        if count <= 0:
            log(f"[{task_id}] uiamark: 0 control (app không expose a11y?) -> vision")
            return None
        try:
            img = await asyncio.wait_for(waiter, 12)
        except asyncio.TimeoutError:
            img = None
        if not img:
            log(f"[{task_id}] uiamark: {count} control nhưng không nhận được ảnh -> vision")
            return None
        self._save(task_id, "uiamarks", img, "jpg")
        note = (f"Ảnh có {count} component TƯƠNG TÁC (nút/ô nhập/link) được đánh số trong "
                f"khung đỏ. Cần bấm vào: '{target}'. Component cần bấm ở số mấy? "
                f"Chỉ trả về MỘT con số, 0 nếu không có. /no_think")
        n = await self._marks_pick(task_id, img, note)
        if not n or not (1 <= n <= count):
            log(f"[{task_id}] uiamark: model không chọn (n={n}/{count}) -> vision")
            return None
        try:
            res2 = await self.relay.call_device("uiapt", {"n": n}, task_id, timeout=UIA_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] uiapt {n}: {e} -> vision")
            return None
        d2 = (res2.get("result") or {}).get("detail", "") or ""
        m2 = re.match(r"\s*(\d+)\s*,\s*(\d+)", d2)
        if not (res2.get("ok") and m2):
            log(f"[{task_id}] uiapt {n}: {d2 or 'none'} -> vision")
            return None
        sx, sy = int(m2.group(1)), int(m2.group(2))
        log(f"[{task_id}] uiamark: chọn #{n}/{count} -> screen({sx},{sy})")
        return sx, sy

    async def _tars_ground(self, task_id: str, target: str, area: str = "any",
                           anchor_kws: list = None):
        """(B) UI-TARS grounding: the GUI-specialist model looks at the screenshot
        and returns the click point for `target`. Returns (sx,sy) in SCREEN pixels,
        or None to fall back to the vision pipeline. Ollama swaps UI-TARS in for
        this one call (qwen is evicted), so it costs a model reload each way."""
        if not (UITARS_ENABLE and self._tars_ok and self._raw_frame and self._scr_w):
            return None
        try:
            from io import BytesIO
            from PIL import Image
        except Exception:  # noqa: BLE001
            return None
        # Scale the frame to a size we control so returned pixel coords map cleanly.
        try:
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] tars: ảnh lỗi ({e}) -> vision")
            return None
        ow, oh = im.size
        if UITARS_FULLRES and self._scr_w and self._scr_h:
            # Send at real screen size: coords come back as screen pixels, so the
            # screen mapping below (coord*scr/sent) collapses to identity.
            sw, sh = self._scr_w, self._scr_h
            if (ow, oh) != (sw, sh):
                im = im.resize((sw, sh))
        else:
            scale = min(1.0, UITARS_MAX_SIDE / max(ow, oh)) if max(ow, oh) else 1.0
            sw, sh = max(1, round(ow * scale)), max(1, round(oh * scale))
            if scale < 1.0:
                im = im.resize((sw, sh))
        buf = BytesIO()
        im.save(buf, format="PNG")
        sent = buf.getvalue()

        hint = ""
        if area and area != "any":
            hint += f" (ở phía {area} màn hình)"
        if anchor_kws:
            hint += f" (gần chữ '{anchor_kws[0]}')"
        prompt = (
            "You are a precise GUI grounding model. Look at the screenshot and find "
            "the single UI element the user wants to interact with. Respond with ONLY "
            "a click action and nothing else.\n\n"
            "Output format: click(start_box='(x,y)') where x and y are pixel "
            "coordinates in the image.\n\n"
            f"Element to click: {target}{hint}")
        try:
            resp = await asyncio.wait_for(
                ollama_client.chat(
                    model=UITARS_MODEL, think=False,
                    options={"num_ctx": UITARS_NUM_CTX, "temperature": 0},
                    messages=[{"role": "user", "content": prompt, "images": [sent]}]),
                timeout=UITARS_TIMEOUT)
            txt = (resp["message"].get("content") or "").strip()
        except asyncio.TimeoutError:
            log(f"[{task_id}] tars: timeout -> vision")
            return None
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            low = msg.lower()
            if "not found" in low or "no such model" in low or "try pulling" in low:
                self._tars_ok = False
                log(f"[{task_id}] tars: model '{UITARS_MODEL}' chưa có trong Ollama "
                    "-> tắt UI-TARS phiên này, dùng vision")
            else:
                log(f"[{task_id}] tars: lỗi ({msg}) -> vision")
            return None

        import re
        nums = re.findall(r"-?\d+\.?\d*", txt)
        if len(nums) < 2:
            log(f"[{task_id}] tars: không đọc được toạ độ từ {txt[:60]!r} -> vision")
            return None
        vals = [float(n) for n in nums]
        if len(vals) >= 4:            # a box -> click its centre
            cx, cy = (vals[0] + vals[2]) / 2, (vals[1] + vals[3]) / 2
        else:
            cx, cy = vals[0], vals[1]
        conv = UITARS_COORD
        if conv == "auto":
            # UI-TARS-1.5 emits ABSOLUTE pixel coords of the sent image (Qwen2.5-VL
            # convention). A value above the sent size can't be a pixel, so that
            # means the quant uses 0..1000 normalised (older UI-TARS). Note small
            # coords are ambiguous: pin UITARS_COORD explicitly after calibrating.
            conv = "norm1000" if (cx > sw * 1.02 or cy > sh * 1.02) else "image"
        if conv == "norm1000":
            sx = round(cx / 1000.0 * self._scr_w)
            sy = round(cy / 1000.0 * self._scr_h)
        else:                         # pixels of the sent frame
            sx = round(cx * self._scr_w / sw)
            sy = round(cy * self._scr_h / sh)
        sx = max(0, min(self._scr_w, sx))
        sy = max(0, min(self._scr_h, sy))
        log(f"[{task_id}] tars raw={txt[:50]!r} pt=({cx:.0f},{cy:.0f}) sent={sw}x{sh} "
            f"conv={conv} -> screen({sx},{sy})")
        return sx, sy

    async def _send_click(self, task_id: str, target: str, sx: int, sy: int, how: str) -> str:
        """Click a point already in SCREEN pixels and report."""
        try:
            res = await self.relay.call_device("click", {"x": sx, "y": sy, "button": 1},
                                               task_id, timeout=DEVICE_TIMEOUT)
        except asyncio.TimeoutError:
            return "error: thiết bị không phản hồi (timeout)"
        except Exception as e:  # noqa: BLE001
            return f"error: {e}"
        if res.get("ok"):
            self._invalidate_screen_cache()
            return f"ok: đã click '{target}' ({how}) tại ({sx},{sy}) — screenshot để xác minh"
        return f"error: {res.get('error', 'click thất bại')}"

    async def _tap(self, task_id: str, args: dict) -> str:
        target = (args.get("target") or "").strip()
        if not target:
            return "error: thiếu 'target'"
        if not self._screen_cache_fresh():
            screenshot_result = await self._screenshot(
                task_id,
                force=False,
                attach=False,
            )
            if screenshot_result.startswith("error") or not self._raw_frame:
                return screenshot_result

        kws = _keywords_from(target, args.get("keywords"))
        area = (args.get("area") or "any").lower()
        control = (args.get("control") or "any").lower()
        anchor_kws = _keywords_from("", args.get("anchor"))
        relation = (args.get("relation") or ("near" if anchor_kws else "on")).lower()
        if relation not in _REL_VI and relation != "on":
            relation = "near"

        # (C) UIAutomation first: the PC resolves the control's REAL screen coords
        # by ControlType and/or Name - exact and scale-free. If it finds it, click
        # straight away and skip all vision. Falls through on canvas/no a11y.
        uia = await self._uia_find(task_id, kws, area, control)
        if uia:
            sx, sy = uia
            if self._img_w and self._scr_w:   # mark on the debug image for review
                self._mark_click(task_id, round(sx * self._img_w / self._scr_w),
                                 round(sy * self._img_h / self._scr_h), f"tap_{target}")
            log(f"[{task_id}] tap '{target}' [uia] -> screen({sx},{sy})")
            return await self._send_click(task_id, target, sx, sy, "uia")

        # Vision prep: OCR the screen once, then resolve the anchor + search region.
        items = await self._ocr_items(task_id) if OCR_ENABLE else None

        # Anchor-relative search: find the anchor text by OCR, then search only
        # the box where the target should be (e.g. bottom of the chat panel
        # headed by a contact name). A small region makes both the OCR match and
        # the 3x3 locate far more accurate - even for a blank input box.
        pt, how, region, ctx = None, "", None, ""
        if anchor_kws:
            rel_vi = _REL_VI.get(relation, "gần")
            cands = self._find_anchor(task_id, items, anchor_kws, area, relation) if items else []
            a = cands[0] if cands else None
            # Ambiguous anchor (e.g. a contact name shown both in a feed post and
            # as a chat-window header): pick the candidate whose relative region
            # actually contains the target keywords ('Aa' -> the chat input).
            if a and relation != "on" and len(cands) > 1 and kws:
                best_c, best_s, best_reg = a, -1.0, None
                for c in cands:
                    reg = _relative_region(c, relation, self._img_w, self._img_h)
                    s = max((max(_sim(k, it["text"]) for k in kws)
                             for it in items if _in_region(it, reg)), default=0.0)
                    if s > best_s:
                        best_s, best_c, best_reg = s, c, reg
                if best_s >= OCR_MATCH_MIN and best_c is not a:
                    a = best_c
                    region = best_reg
                    log(f"[{task_id}] anchor đa ứng viên -> chọn @({a['cx']},{a['cy']}) "
                        f"theo keyword khớp ({best_s:.2f})")
            if a and relation == "on":
                pt, how = (a["cx"], a["cy"]), "anchor"
            elif a:
                if region is None:
                    region = _relative_region(a, relation, self._img_w, self._img_h)
                ctx = f"(ảnh này là vùng {rel_vi} '{a['text']}')"
                log(f"[{task_id}] search region {relation} anchor: {region}")
                self._save_region(task_id, a, region)
            else:   # anchor not read by OCR: still tell the model where to look
                ctx = f"(phần tử nằm {rel_vi} chữ '{anchor_kws[0]}')"

        # Hybrid grounding (phương án ①): a VERY confident OCR keyword match wins
        # BEFORE UI-TARS. A text-labelled target ("Aa", "Gửi") is read more
        # precisely by OCR than a VLM points at it; UI-TARS then covers the
        # text-less / icon targets it is best at. 'anchor on' already set pt above.
        if pt is None and items:
            ex = await self._tap_ocr(task_id, target, kws, area, items, region,
                                     thr=OCR_EXACT_MIN)
            if ex:
                pt, how = ex, "OCR-exact"

        # (B) UI-TARS grounding: the GUI-specialist model locates the element
        # straight from the screenshot. Ollama swaps it in for this call (qwen is
        # evicted) and returns real screen coords. None -> fall through to vision.
        if pt is None:
            tg = await self._tars_ground(task_id, target, area, anchor_kws)
            if tg:
                sx, sy = tg
                if self._img_w and self._scr_w:
                    self._mark_click(task_id, round(sx * self._img_w / self._scr_w),
                                     round(sy * self._img_h / self._scr_h), f"tap_{target}")
                log(f"[{task_id}] tap '{target}' [ui-tars] -> screen({sx},{sy})")
                return await self._send_click(task_id, target, sx, sy, "ui-tars")

        # UIA Set-of-Mark: let the LLM pick among numbered interactive controls
        # (exact a11y boxes). Strong on browser UIs; no-ops when no controls.
        if pt is None:
            um = await self._uia_marks(task_id, target, area)
            if um:
                sx, sy = um
                if self._img_w and self._scr_w:
                    self._mark_click(task_id, round(sx * self._img_w / self._scr_w),
                                     round(sy * self._img_h / self._scr_h), f"tap_{target}")
                log(f"[{task_id}] tap '{target}' [uia-marks] -> screen({sx},{sy})")
                return await self._send_click(task_id, target, sx, sy, "uia-marks")

        # Full OCR keyword match (>= OCR_MATCH_MIN), then Set-of-Mark, then 3x3 grid.
        if pt is None and items:
            pt = await self._tap_ocr(task_id, target, kws, area, items, region)
            how = "OCR"
        if pt is None:                       # Set-of-Mark: OCR + OpenCV boxes, pick by number
            mp = await self._tap_marks(task_id, target, kws, area, items, region)
            if mp:
                pt, how = mp, "marks"
        if pt is None:
            pt = await self._locate(task_id, target, kws, area, region, ctx)
            how = "locate" + ("+region" if region else "")
        if not pt:
            return f"error: không tìm thấy '{target}' trên màn hình (thử screenshot lại)"

        ix, iy = pt
        if self._img_w and self._scr_w:
            sx = round(ix * self._scr_w / self._img_w)
            sy = round(iy * self._scr_h / self._img_h)
        else:
            sx, sy = ix, iy
        log(f"[{task_id}] tap '{target}' [{how}] img({ix},{iy}) -> screen({sx},{sy})")
        self._mark_click(task_id, ix, iy, f"tap_{target}")
        return await self._send_click(task_id, target, sx, sy, how)

    async def _refine_point(self, task_id: str, x: int, y: int) -> tuple:
        """Coarse-to-fine: crop a generous box around the model's rough (x,y),
        zoom it, grid it with ORIGINAL-image coordinate labels, and ask the model
        for the exact point. Returns refined (x,y) in original image space, or the
        input unchanged on any failure. One extra LLM call per click."""
        if not (REFINE_CLICK and self._raw_frame and self._img_w):
            return x, y
        try:
            from io import BytesIO
            from PIL import Image, ImageDraw
        except Exception:  # noqa: BLE001
            return x, y
        W, H = self._img_w, self._img_h
        # Generous crop (half the image) so an off-target coarse guess still
        # contains the real element; centre it on (x,y), clamp to bounds.
        cw, ch = W // 2, H // 2
        x0 = max(0, min(W - cw, x - cw // 2))
        y0 = max(0, min(H - ch, y - ch // 2))
        try:
            im = Image.open(BytesIO(self._raw_frame)).convert("RGB").crop((x0, y0, x0 + cw, y0 + ch))
            Z = 2
            im = im.resize((cw * Z, ch * Z))
            d = ImageDraw.Draw(im)
            red = (255, 40, 40)
            stp = 50  # label every 50 original px
            gx = (x0 // stp + 1) * stp
            while gx < x0 + cw:
                px = (gx - x0) * Z
                d.line([(px, 0), (px, ch * Z)], fill=red, width=1)
                d.text((px + 1, 1), str(gx), fill=red)
                gx += stp
            gy = (y0 // stp + 1) * stp
            while gy < y0 + ch:
                py = (gy - y0) * Z
                d.line([(0, py), (cw * Z, py)], fill=red, width=1)
                d.text((1, py + 1), str(gy), fill=red)
                gy += stp
            out = BytesIO()
            im.save(out, format="PNG")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] refine crop failed: {e}")
            return x, y

        note = (f"Nhiệm vụ: {self._task_text}. Bạn vừa chọn thô điểm ~({x},{y}). Đây là ảnh "
                f"PHÓNG TO vùng quanh đó; nhãn lưới đỏ = toạ độ PIXEL TRÊN ẢNH GỐC. Nhìn kỹ "
                "và trả về DUY NHẤT toạ độ chính xác của phần tử cần tương tác, dạng: x,y "
                "(pixel ảnh gốc). Không giải thích. /no_think")
        try:
            resp = await ollama_client.chat(
                model=MODEL, think=THINK, options={"num_ctx": NUM_CTX},
                messages=[{"role": "user", "content": note, "images": [out.getvalue()]}])
            txt = (resp["message"].get("content") or "")
            import re
            m = re.search(r"(\d{1,4})\s*[,; ]\s*(\d{1,4})", txt)
            if m:
                rx = max(x0, min(x0 + cw, int(m.group(1))))
                ry = max(y0, min(y0 + ch, int(m.group(2))))
                log(f"[{task_id}] refine: ({x},{y}) -> ({rx},{ry}) [{txt.strip()[:40]}]")
                return rx, ry
            log(f"[{task_id}] refine: no coords parsed from {txt.strip()[:60]!r}")
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] refine LLM failed: {e}")
        return x, y

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
        waiter = self.relay.arm_frame()   # arm BEFORE the call to avoid a race
        try:
            log(f"[{task_id}] screenshot: enqueue aishot, waiting device_result ...")
            res = await self.relay.call_device("screenshot", {}, task_id,
                                               timeout=DEVICE_TIMEOUT)
        except asyncio.TimeoutError:
            log(f"[{task_id}] screenshot: device_result TIMEOUT (PC agent chạy chưa?)")
            return "error: thiết bị không phản hồi (timeout)"
        except Exception as e:  # noqa: BLE001
            log(f"[{task_id}] screenshot: device error {e}")
            return f"error: {e}"
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

    async def _ask_confirm(self, task_id: str, name: str, args: dict) -> bool:
        """Block until the UI answers a confirm_request, or time out (deny)."""
        cid = f"{task_id}:{name}:{time.time()}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._confirm_waiters[cid] = fut
        await self.relay.send_ui({"type": "confirm_request", "task_id": task_id,
                                  "confirm_id": cid, "tool": name, "arguments": args,
                                  "message": f"Xác nhận hành động: {name} {args}"})
        # on_confirm() resolves this future when the UI answers. No answer in 60s
        # -> deny (fail safe).
        try:
            return await asyncio.wait_for(fut, 60)
        except asyncio.TimeoutError:
            return False
        finally:
            self._confirm_waiters.pop(cid, None)


async def main():
    relay = RelayClient(RELAY_URL, AGENT_TOKEN, CA_FILE)
    TaskRunner(relay)  # registers chat/cancel handlers
    print(f"[agent] model={MODEL} relay={RELAY_URL}")
    print(f"[agent] grounding: uia={UIA_ENABLE} uia_marks={UIA_MARKS_ENABLE} "
          f"ocr={OCR_ENABLE} ui-tars="
          + (f"{UITARS_MODEL}" if UITARS_ENABLE else "off"))
    await relay.run_forever()


if __name__ == "__main__":
    asyncio.run(main())
