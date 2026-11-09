"""Keyword heuristics that classify the user's request and the model's reply."""


# Words that mean the task includes an OUTWARD delivery step (send/post/comment),
# not just reading/searching. Used to stop the agent ending right after it has
# gathered info but before it has actually sent anything.
_DELIVER_INTENTS = ("gửi", "gởi", "nhắn", "nhắn tin", "trả lời", "đăng",
                    "bình luận", "comment", "reply", "soạn tin", "gửi tin")


# Contain "đăng" but are not outward posts.
_NOT_DELIVERY = ("đăng nhập", "đăng ký", "đăng kí", "đăng xuất")


def _needs_delivery(text: str) -> bool:
    """Cheap first pass: MAY this request include sending something? Words like
    "trả lời" are ambiguous, so the runner confirms a hit with the LLM."""
    t = (text or "").lower()
    for phrase in _NOT_DELIVERY:
        t = t.replace(phrase, " ")
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


def parse_yes_no(reply: str) -> bool:
    """True when a CO/KHONG classifier reply says yes. Anything unclear is no."""
    words = (reply or "").strip().lower().split()
    return bool(words) and words[0].strip(".,:!\"'") in ("co", "có", "yes")
