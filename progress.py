"""Notices a task going round in circles: screen-changing actions after which the
next screenshot looks the same as the previous one (scrolling past the end of a
document, clicking something inert, a window that is not in front)."""
from imaging import frame_signature, frames_similar

STUCK_HINT = (
    "ĐỪNG lặp lại thao tác đó. Đổi cách: tới cuối/đầu tài liệu dùng hotkey ctrl+end / "
    "ctrl+home; trang nằm cạnh nhau hoặc cuộn bị chạy ngang thì chỉnh chế độ xem về một "
    "trang dọc; kiểm tra cửa sổ cần dùng có đang ở trước và chuột có nằm trên vùng cần "
    "cuộn; không biết làm gì tiếp thì ask_user.")


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
