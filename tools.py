"""Tool schemas exposed to the LLM, and the mapping to device calls.

These are the ONLY actions the agent can drive. The firmware (ai_link.c) has an
identical allowlist and re-validates every argument, so a wrong tool choice by
the model is rejected device-side too - the schema here is convenience, not the
security boundary.

`is_dangerous` picks the calls the user must confirm before they run
(ai_link also refuses nothing here - confirmation is enforced by the agent, so
keep this list and the firmware's intent in sync).
"""
import re
import unicodedata
from typing import Any

# Ollama / OpenAI-style tool definitions.
TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "move",
            "description": "Move the mouse cursor to an absolute screen pixel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "screen x pixel"},
                    "y": {"type": "integer", "description": "screen y pixel"},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "Click at an absolute screen pixel. button 1=left 2=right 4=middle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer"},
                    "y": {"type": "integer"},
                    "button": {"type": "integer", "enum": [1, 2, 4]},
                },
                "required": ["x", "y"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tap",
            "description": ("Click vào một phần tử mô tả bằng lời (vd 'ô nhập tin nhắn "
                           "Messenger', 'nút Gửi', 'liên hệ Trường'). Hệ thống tìm chữ trên "
                           "màn hình (OCR) theo keywords để định vị chính xác. ƯU TIÊN dùng "
                           "tap thay cho click toạ độ khi cần bấm vào phần tử trên màn hình. "
                           "Điền keywords là các chữ NHÌN THẤY trên/ngay cạnh phần tử; phần "
                           "tử không có chữ thì để trống và mô tả rõ trong target."),
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {"type": "string",
                               "description": "mô tả ngắn gọn phần tử cần bấm"},
                    "keywords": {
                        "type": "array", "items": {"type": "string"},
                        "description": ("các chữ hiển thị trên hoặc sát phần tử, đúng như trên "
                                        "màn hình: tên người ('Trường', 'Phạm Nhật Trường'), "
                                        "placeholder ('Aa', 'Nhắn tin'), nhãn nút ('Gửi', "
                                        "'Send'), tiêu đề... Càng cụ thể càng tốt."),
                    },
                    "area": {
                        "type": "string",
                        "enum": ["any", "left", "right", "top", "bottom", "center"],
                        "description": "vùng màn hình chứa phần tử (nếu biết), mặc định any",
                    },
                    "anchor": {
                        "type": "array", "items": {"type": "string"},
                        "description": ("chữ của một phần tử MỐC dễ thấy gần mục tiêu, dùng khi "
                                        "mục tiêu không có chữ hoặc dễ nhầm. Vd tên người ở "
                                        "đầu khung chat ['Trường'], ô 'Aa' khi muốn bấm nút gửi."),
                    },
                    "relation": {
                        "type": "string",
                        "enum": ["on", "near", "below", "above", "left_of", "right_of",
                                 "bottom_of"],
                        "description": ("vị trí mục tiêu so với anchor: below/above/left_of/"
                                        "right_of; bottom_of = phần dưới cùng của khung nằm "
                                        "dưới anchor (vd ô nhập ở đáy khung chat); on = bấm "
                                        "chính anchor; near = quanh anchor."),
                    },
                    "control": {
                        "type": "string",
                        "enum": ["any", "edit", "button", "link", "listitem", "menuitem",
                                 "tab", "checkbox", "radio", "combobox"],
                        "description": ("LOẠI control (UI Automation) nếu biết, giúp bấm chính "
                                        "xác tuyệt đối không cần nhìn ảnh: edit = ô nhập text, "
                                        "button = nút, link = liên kết, listitem = mục danh "
                                        "sách. Hữu ích khi tên động/không rõ: vd ô nhập tin "
                                        "nhắn là 'edit' ở area 'right' — có thể bỏ keywords. "
                                        "Mặc định any (tìm theo keywords)."),
                    },
                },
                "required": ["target"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scroll",
            "description": "Mouse wheel. Positive scrolls up, negative down (notches).",
            "parameters": {
                "type": "object",
                "properties": {"amount": {"type": "integer"}},
                "required": ["amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "type",
            "description": ("Type a text string (Unicode) at the current focus. Any "
                           "length on a single line; long text is split automatically."),
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "key",
            "description": ("Press one key. name is one of: enter tab esc backspace "
                           "delete space up down left right home end win."),
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hotkey",
            "description": ("Nhấn một tổ hợp phím. combo nối bằng dấu '+', ví dụ: "
                           "ctrl+c, ctrl+v, ctrl+s, ctrl+a, ctrl+z, alt+tab, "
                           "ctrl+shift+esc, win+e, win+r. Phím cuối có thể là chữ/số "
                           "hoặc tên (enter/tab/esc/...)."),
            "parameters": {
                "type": "object",
                "properties": {"combo": {"type": "string", "description": "vd 'ctrl+s'"}},
                "required": ["combo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "screenshot",
            "description": ("Ask the PC to capture the screen and upload it to the "
                           "device. Use to verify the result of an action."),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "zoom",
            "description": ("Chụp RIÊNG một vùng màn hình ở độ phân giải thật để ĐỌC chữ nhỏ "
                           "(tên file trên thanh tiêu đề, nút trên ribbon/thanh công cụ, kết "
                           "quả tìm kiếm, tên người, số liệu). Trả về ảnh vùng đó (lượt sau) và "
                           "chữ đọc được. Chỉ để đọc; muốn bấm thì dùng tap với keywords là chữ "
                           "vừa đọc. Chọn area theo tên, hoặc box [x0,y0,x1,y1] theo lưới của "
                           "ảnh screenshot gần nhất."),
            "parameters": {
                "type": "object",
                "properties": {
                    "area": {
                        "type": "string",
                        "enum": ["title", "top_bar", "top", "bottom", "left", "right", "center",
                                 "top_left", "top_right", "bottom_left", "bottom_right",
                                 "taskbar"],
                        "description": ("title = thanh tiêu đề cửa sổ; top_bar = tiêu đề + "
                                        "ribbon/thanh công cụ; các vùng còn lại theo tên"),
                    },
                    "box": {"type": "array", "items": {"type": "integer"},
                            "description": "[x0,y0,x1,y1] trên ảnh screenshot gần nhất"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": ("Hỏi người dùng và CHỜ câu trả lời rồi làm tiếp. Dùng khi phân vân: "
                           "nhiều file/người/mục trùng hoặc gần giống nhau, không rõ nền tảng, "
                           "không tìm thấy thứ được yêu cầu. Có lựa chọn thì liệt kê trong "
                           "options (mỗi lựa chọn đủ để phân biệt: tên + thông tin phụ). "
                           "KHÔNG tự đoán thay người dùng."),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "câu hỏi ngắn"},
                    "options": {"type": "array", "items": {"type": "string"},
                                "description": "2-8 lựa chọn; để trống nếu hỏi tự do"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": ("Tìm thông tin trên Internet (chạy ngay trên máy chủ, KHÔNG "
                           "điều khiển PC). Dùng khi cần dữ kiện mới/không chắc: giá cả, "
                           "tin tức, số liệu, định nghĩa cập nhật... Trả về vài kết quả "
                           "(tiêu đề + trích đoạn + link). Đọc kết quả rồi trả lời và nêu "
                           "nguồn. KHÔNG dùng cho thao tác trên máy."),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "câu truy vấn tìm kiếm"},
                    "max_results": {"type": "integer",
                                    "description": "số kết quả muốn lấy (mặc định 5)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": ("Mở một đường link và lấy nội dung văn bản của trang (chạy "
                           "trên máy chủ). Dùng khi cần đọc sâu một kết quả từ web_search "
                           "để bổ sung chi tiết. Trả về phần chữ chính của trang."),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "đường link http/https"},
                },
                "required": ["url"],
            },
        },
    },
]

TOOL_NAMES = {t["function"]["name"] for t in TOOLS}

# Single keys outside this list need the user's confirmation. Hotkeys never do
# (the user's choice, for full automation): what a shortcut opens, such as the Run
# box, is harmless until text is typed into it, and `type` is still checked.
SAFE_KEYS = frozenset({
    "enter", "tab", "esc", "escape", "backspace", "delete", "del", "space",
    "up", "down", "left", "right", "home", "end",
})

# Free text cannot be allowlisted (it is what gets sent to people), so `type` is
# checked for what would run as a command once Enter is pressed.
_RISKY_FIRST_WORDS = frozenset({
    "cmd", "powershell", "pwsh", "wscript", "cscript", "mshta", "rundll32",
    "regsvr32", "certutil", "bitsadmin", "curl", "wget", "schtasks", "taskkill",
    "shutdown", "del", "rd", "rmdir", "rm", "format", "reg", "msiexec", "iex",
    "iwr", "invoke-expression", "invoke-webrequest",
})
_RISKY_TEXT = re.compile(
    r"powershell|\bcmd(\.exe|\s+/[ck]\b)|\s-enc(odedcommand)?\b|invoke-(expression|webrequest)"
    r"|\biex\b|&&|https?://\S+\.(ps1|bat|cmd|exe|vbs|hta)\b",
    re.IGNORECASE,
)


# The firmware's command line holds 95 bytes including "type ", and truncates
# silently beyond that.
TYPE_MAX_BYTES = 90
_JOINERS = {"\u200d", "\ufe0f"}


def _can_split_before(text: str, i: int) -> bool:
    """The PC agent trims each command, so a space at a chunk edge would be lost;
    a combining mark or emoji joiner must stay with the character before it."""
    before, after = text[i - 1], text[i]
    return not (before.isspace() or after.isspace() or unicodedata.combining(after)
                or before in _JOINERS or after in _JOINERS)


def split_for_type(text: str, max_bytes: int = TYPE_MAX_BYTES) -> list[str]:
    """Cut text into chunks of at most max_bytes UTF-8 bytes that, pasted one after
    another, reproduce it exactly. Raises ValueError if no safe cut exists."""
    text = unicodedata.normalize("NFC", text)
    chunks, start, used, last_ok = [], 0, 0, 0
    i = 0
    while i < len(text):
        size = len(text[i].encode("utf-8"))
        if i > start and _can_split_before(text, i):
            last_ok = i
        if used + size > max_bytes:
            if last_ok <= start:
                raise ValueError("không có điểm cắt an toàn")
            chunks.append(text[start:last_ok])
            start, used, i = last_ok, 0, last_ok
            continue
        used += size
        i += 1
    chunks.append(text[start:])
    return chunks


def _first_command_word(text: str) -> str:
    """'"C:\\Windows\\cmd.exe" /c x' -> 'cmd'."""
    parts = text.strip().strip("\"'").split()
    if not parts:
        return ""
    word = re.split(r"[\\/]", parts[0].strip("\"'").lower())[-1]
    return word[:-4] if word.endswith(".exe") else word


def is_dangerous(tool: str, args: dict) -> bool:
    if tool == "key":
        return str(args.get("name", "")).lower() not in SAFE_KEYS
    if tool == "type":
        text = str(args.get("text", ""))
        return ("\n" in text or "\r" in text
                or _first_command_word(text) in _RISKY_FIRST_WORDS
                or bool(_RISKY_TEXT.search(text)))
    return False
