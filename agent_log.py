"""Console logging, mirrored to the browser chat panel."""
import asyncio
import time

from config import DEBUG

_log_q = None
_current_task = ""


def attach_queue():
    """Create the queue log() mirrors into. Needs a running event loop."""
    global _log_q
    _log_q = asyncio.Queue(maxsize=1000)
    return _log_q


def set_task(task_id: str) -> None:
    global _current_task
    _current_task = task_id


def current_task() -> str:
    return _current_task


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    q = _log_q
    if q is not None:
        try:
            q.put_nowait(msg)
        except Exception:  # noqa: BLE001 - queue full / not ready: drop the line
            pass


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
