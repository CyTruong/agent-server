"""Text matching for locating on-screen elements. Pure functions, no I/O."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher


def _norm(s: str) -> str:
    """Lowercase, strip Vietnamese accents (đ->d), collapse spaces - so OCR
    misreads of diacritics still match ('Truong' == 'Trường')."""
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


def _sim(keyword: str, text: str) -> float:
    """Fuzzy similarity of a keyword to an OCR text box (0..1)."""
    k, t = _norm(keyword), _norm(text)
    if not k or not t:
        return 0.0
    if k == t:
        return 1.0
    # Whole words only: "Aa" must not match "Aaron".
    if len(k) >= 2 and re.search(rf"(?<!\w){re.escape(k)}(?!\w)", t):
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


# --- Proper names ------------------------------------------------------------
# A wrong accent is a different person here ("Trường"/"Tường", "Tâm"/"Tám"), so
# names get their own accent-aware, whole-word matching instead of _sim().

EXACT, SUFFIX, PARTIAL, ACCENT, FUZZY, NONE = (
    "exact", "suffix", "partial", "accent", "fuzzy", "none")
_RANK = {EXACT: 5, SUFFIX: 4, PARTIAL: 3, ACCENT: 2, FUZZY: 1, NONE: 0}
CERTAIN_MATCHES = (EXACT, SUFFIX)
_FUZZY_MIN = 0.8

# Capitalised words on screen that are UI labels or products, never people.
_UI_LABELS = frozenset(_norm(w) for w in (
    "Gửi", "Send", "Aa", "Tìm kiếm", "Search", "Nhắn tin", "Bình luận", "Comment",
    "Thích", "Like", "Chia sẻ", "Share", "Trả lời", "Reply", "Đăng", "Post",
    "Trang chủ", "Home", "Bạn bè", "Nhóm", "Thông báo", "Menu", "Video",
    "Marketplace", "Kỷ niệm", "Đã lưu", "Lưu", "Save", "Mở", "Open", "Đóng",
    "Close", "Huỷ", "Hủy", "Cancel", "OK", "Có", "Không", "Messenger", "Facebook",
    "Zalo", "Chrome", "Edge", "Google", "Windows", "Notepad", "Explorer", "File",
))
_PERSON_CUES = ("liên hệ", "người", "tên", "bạn", "anh", "chị", "em", "cô", "chú", "bác",
                "cuộc trò chuyện", "hội thoại", "kết quả tìm kiếm")


def _words(s: str) -> list:
    """Case-folded whole words, accents kept."""
    return re.findall(r"\w+", unicodedata.normalize("NFC", s or "").casefold())


def _seq_at(needle: list, hay: list) -> int:
    """Index of the word sequence `needle` inside `hay`, or -1."""
    for i in range(len(hay) - len(needle) + 1):
        if hay[i:i + len(needle)] == needle:
            return i
    return -1


def _looks_like_name(text: str) -> bool:
    """A full name is a few capitalised words. Message previews ('Bạn: mai gặp An')
    and placeholders ('Nhập @, tin nhắn tới An') that merely end in it are not."""
    parts = (text or "").split()
    return (len(parts) <= 6 and not any(c in text for c in ":@,?!")
            and all(p[:1].isupper() for p in parts))


def name_match(name: str, text: str) -> str:
    """How `text` (read off the screen) relates to the person's `name`:
    exact, suffix (ends with it: 'Phạm Nhật Trường' for 'Trường'), partial (has
    it elsewhere: 'Trường Đại học'), accent (same letters, different accents),
    fuzzy (a near spelling) or none."""
    n, t = _words(name), _words(text)
    if not n or not t:
        return NONE
    if n == t or "".join(n) == "".join(t):   # OCR often drops a space: "AVinh"
        return EXACT
    i = _seq_at(n, t)
    if i >= 0:
        return SUFFIX if i + len(n) == len(t) and _looks_like_name(text) else PARTIAL
    bare_n, bare_t = [_norm(w) for w in n], [_norm(w) for w in t]
    if _seq_at(bare_n, bare_t) >= 0 or "".join(bare_n) == "".join(bare_t):
        return ACCENT
    joined = " ".join(bare_n)
    ratio = max([SequenceMatcher(None, joined, " ".join(bare_t)).ratio()]
                + [SequenceMatcher(None, joined, w).ratio() for w in bare_t])
    return FUZZY if ratio >= _FUZZY_MIN else NONE


@dataclass
class NameResolution:
    """status: 'certain' (click it), 'uncertain' (ask first; box is the best
    guess) or 'none' (the name is not on screen)."""
    status: str
    box: dict | None = None
    candidates: list = field(default_factory=list)   # every matching box, best first


def name_rank(names: list, text: str) -> int:
    """Quality of the best match of any of `names` in `text`; higher is better."""
    return max((_RANK[name_match(n, text)] for n in names), default=0)


def is_lossy_read(names: list, text: str) -> bool:
    """Could `text` be the name with its accents or letters misread by OCR? Other
    mismatches ('Trường Đại học') are different text, so re-reading cannot help."""
    return 0 < name_rank(names, text) <= _RANK[ACCENT]


def resolve_name(names: list, items: list) -> NameResolution:
    """Find the box for a person among OCR `items`."""
    scored = []
    for item in items or []:
        quality = max((name_match(n, item.get("text", "")) for n in names),
                      key=_RANK.__getitem__, default=NONE)
        if quality != NONE:
            scored.append((quality, item))
    if not scored:
        return NameResolution("none")
    scored.sort(key=lambda pair: -_RANK[pair[0]])
    boxes = [item for _quality, item in scored]
    top_quality = scored[0][0]
    if top_quality == EXACT:
        return NameResolution("certain", boxes[0], boxes)
    if top_quality == SUFFIX:
        # One full name ending in it is the person; two different ones is ambiguous.
        suffixes = {" ".join(_words(i["text"])) for q, i in scored if q == SUFFIX}
        status = "certain" if len(suffixes) == 1 else "uncertain"
        return NameResolution(status, boxes[0], boxes)
    return NameResolution("uncertain", boxes[0], boxes)


def is_proper_name(keyword: str, request: str, target: str = "") -> bool:
    """Does this tap keyword name a person/group? Then it needs strict matching.
    True for a non-UI-label keyword that is taken from the user's request and
    capitalised, or that the target describes as a person."""
    keyword = (keyword or "").strip()
    if len(keyword) < 2 or _norm(keyword) in _UI_LABELS or any(c.isdigit() for c in keyword):
        return False
    words = _words(keyword)
    capitalised = all(w[:1].isupper() for w in keyword.split())
    request_words = _words(request)
    # Accent-blind on purpose: this only decides whether to apply the strict check,
    # and the user may have typed "truong" for "Trường".
    in_request = (_seq_at(words, request_words) >= 0
                  or _seq_at([_norm(w) for w in words], [_norm(w) for w in request_words]) >= 0)
    target_words = _words(target)
    person_cue = any(_seq_at(_words(cue), target_words) >= 0 for cue in _PERSON_CUES)
    return (capitalised and in_request) or (person_cue and (capitalised or in_request))
