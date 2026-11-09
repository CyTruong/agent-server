"""Tool schemas exposed to the LLM, and the mapping to device calls.

These are the ONLY actions the agent can drive. The firmware (ai_link.c) has an
identical allowlist and re-validates every argument, so a wrong tool choice by
the model is rejected device-side too - the schema here is convenience, not the
security boundary.

`DANGEROUS` lists calls that must be confirmed by the user before running
(ai_link also refuses nothing here - confirmation is enforced by the agent, so
keep this list and the firmware's intent in sync).
"""
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
                           "LUÔN điền keywords là các chữ NHÌN THẤY trên/ngay cạnh phần tử."),
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
                "required": ["target", "keywords"],
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
            "description": "Type a short text string (Unicode) at the current focus.",
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

# Confirm with the user before executing. win can open the Run dialog; typing
# newlines or shell words can execute commands.
DANGEROUS = {"key:win"}


def is_dangerous(tool: str, args: dict) -> bool:
    if tool == "key" and args.get("name", "").lower() == "win":
        return True
    if tool == "hotkey":
        combo = args.get("combo", "").lower().replace(" ", "")
        # Run dialog, force-close, task manager, logoff/shutdown-ish combos.
        if combo in ("win+r", "alt+f4", "ctrl+alt+del", "ctrl+alt+delete",
                     "win+x", "ctrl+shift+esc"):
            return True
        if "win" in combo.split("+"):
            return True
    if tool == "type":
        text = args.get("text", "")
        if "\n" in text or "\r" in text:
            return True
        low = text.strip().lower()
        if low in ("cmd", "powershell", "pwsh") or low.startswith(("del ", "format ", "rm ")):
            return True
    return False
