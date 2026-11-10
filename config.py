"""All environment-driven settings. Import this first: it loads .env, and other
modules (web_search) read os.environ at import time."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

RELAY_URL = os.environ.get("RELAY_URL", "")
AGENT_TOKEN = os.environ.get("AGENT_TOKEN", "")
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
# Screenshots kept in the conversation; older ones are dropped so every turn
# does not re-send them all and overflow num_ctx.
KEEP_IMAGES = int(os.environ.get("KEEP_IMAGES", "2"))
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
# nothing or the target has no text. Needs easyocr (CPU); see ocr.py.
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
# For a target with no text keywords, ask UI-TARS before the other vision layers.
# Targets with keywords always try OCR first. Checking people's names by OCR comes
# before all of it: UI-TARS returns only a point and cannot tell two names apart.
UITARS_BEFORE_OCR = os.environ.get("UITARS_BEFORE_OCR", "true").lower() in ("1", "true", "yes")
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
# A VERY confident OCR keyword match (>= this) is clicked directly (before UI-TARS
# when UITARS_BEFORE_OCR=false). A text-labelled target ("Aa", "Gửi") is read more precisely by OCR than
# a VLM points at it, so OCR wins first; UI-TARS handles the text-less/icon cases.
OCR_EXACT_MIN = float(os.environ.get("OCR_EXACT_MIN", "0.8"))

# qwen3-vl is a "thinking" model: left on, it burns the whole generation on a
# reasoning monologue and never emits the tool call (blank turns, 80s/step).
# Off = respond directly with tool calls. Set OLLAMA_THINK=true to re-enable.
THINK = os.environ.get("OLLAMA_THINK", "false").lower() in ("1", "true", "yes")

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
# How long ask_user waits for the user's answer. The wait does not count toward
# TASK_TIMEOUT_SECONDS.
CHOICE_TIMEOUT = float(os.environ.get("CHOICE_TIMEOUT_SECONDS", "180"))
# Short-term context: the last CONTEXT_TURNS finished requests of a device, if
# younger than CONTEXT_TTL_SECONDS, are shown to the next request so it can say
# "sửa file đó" or "nhắn thêm cho anh ấy". Memory only, never written to disk.
CONTEXT_TTL = float(os.environ.get("CONTEXT_TTL_SECONDS", "900"))
CONTEXT_TURNS = int(os.environ.get("CONTEXT_TURNS", "3"))
CONTEXT_RESULT_CHARS = int(os.environ.get("CONTEXT_RESULT_CHARS", "600"))
# Stop a task after this many screenshots in a row showed no change after the
# actions before them (e.g. scrolling past the end of a document). 0 = never.
STUCK_LIMIT = int(os.environ.get("STUCK_LIMIT", "3"))
SCREEN_CACHE_TTL = float(os.environ.get("SCREEN_CACHE_TTL_SECONDS", "6.0"))
OCR_CACHE_TTL = float(os.environ.get("OCR_CACHE_TTL_SECONDS", "6.0"))
SCREEN_MUTATING_TOOLS = frozenset({
    "move",
    "click",
    "tap",
    "scroll",
    "type",
    "key",
    "hotkey",
})
