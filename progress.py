"""Notices a task going round in circles: screen-changing actions after which the
next screenshot looks the same as the previous one (scrolling past the end of a
document, clicking something inert, a window that is not in front)."""
import re
import unicodedata
from difflib import SequenceMatcher

from imaging import frame_signature, frames_similar

STUCK_HINT = (
    "ĐỪNG lặp lại thao tác đó; xem ẢNH để hiểu vì sao. Gợi ý theo đúng điều thấy: cuộn "
    "trong tài liệu mà không đổi → đã tới cuối (hoặc dùng ctrl+end / ctrl+home); ảnh CÓ "
    "2+ trang nằm cạnh nhau → chỉnh chế độ xem về một trang dọc; cửa sổ cần dùng không ở "
    "trước → đưa nó lên; có hộp thoại → đọc và đóng nó. Không rõ → ask_user.")


class ProgressWatch:
    def __init__(self, limit: int):
        self.limit = limit
        self.stuck = 0                 # consecutive screenshots that showed no change
        self._signature = None
        self._actions: list[str] = []  # screen-changing tools since the last screenshot

    def action(self, tool: str) -> None:
        self._actions.append(tool)

    def screenshot(self, frame: bytes) -> str:
        """Record a new screenshot. Returns a warning for the model when the actions
        since the previous screenshot changed nothing, else ""."""
        signature = frame_signature(frame)
        warning = ""
        if signature is not None and self._signature is not None and self._actions:
            if frames_similar(self._signature, signature):
                self.stuck += 1
                done = ", ".join(dict.fromkeys(self._actions))
                warning = (f"CẢNH BÁO: màn hình KHÔNG thay đổi sau {done} "
                           f"(lần {self.stuck}/{self.limit}). {STUCK_HINT}")
            else:
                self.stuck = 0
        if signature is not None:
            self._signature = signature
        self._actions = []
        return warning

    @property
    def exhausted(self) -> bool:
        return self.limit > 0 and self.stuck >= self.limit


def _line_key(line: str) -> str:
    text = unicodedata.normalize("NFKD", line.replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]+", "", text)


class ReadingTracker:
    """Remembers the text of the pages read so far, so it survives screenshots
    being pruned from the conversation, and notices when moving through the
    document brings nothing new (the end was reached: Word keeps scrolling into
    grey space, so the screen still changes)."""

    MOVES = ("scroll", "hotkey", "key")

    def __init__(self):
        self._seen: list[str] = []
        self._moved = False
        self.stale = 0          # consecutive moves that showed no new text

    def action(self, tool: str) -> None:
        if tool in self.MOVES:
            self._moved = True

    def _is_seen(self, key: str) -> bool:
        if key in self._seen:
            return True
        return any(SequenceMatcher(None, key, old).ratio() >= 0.85 for old in self._seen
                   if abs(len(old) - len(key)) <= max(4, len(key) // 5))

    def page(self, lines: list[str]) -> tuple[list[str], bool]:
        """Record a page's lines. Returns (lines not seen before, nothing_new)
        where nothing_new is only reported after a move."""
        fresh = []
        for line in lines:
            key = _line_key(line)
            if len(key) < 3 or self._is_seen(key):
                continue
            self._seen.append(key)
            fresh.append(line)
        # Nothing new: no fresh line at all, or on a full page only a stray one or
        # two (a page number, OCR noise) out of ten or more.
        nothing_new = self._moved and (
            not fresh or (len(lines) >= 10 and len(fresh) <= len(lines) // 10))
        if self._moved:
            self.stale = self.stale + 1 if nothing_new else 0
        self._moved = False
        return fresh, nothing_new
