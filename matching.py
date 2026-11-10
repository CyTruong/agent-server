"""Text rules for taps and people's names. Pure functions, no I/O."""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def _norm(s: str) -> str:
    """Lowercase, strip Vietnamese accents (đ->d), collapse spaces ('Truong' ==
    'Trường'), for comparisons where accents must not matter."""
    s = (s or "").replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return " ".join(s.lower().split())


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


# Where the target sits relative to a visible text anchor.
_REL_VI = {
    "near": "gần", "below": "bên dưới", "above": "bên trên",
    "left_of": "bên trái", "right_of": "bên phải",
    "bottom_of": "ở phần dưới cùng của khung bên dưới",
}


# --- Proper names ------------------------------------------------------------
# A wrong accent is a different person here ("Trường"/"Tường", "Tâm"/"Tám"), so
# names get their own accent-aware, whole-word matching.

EXACT, SUFFIX, PARTIAL, ACCENT, FUZZY, NONE = (
    "exact", "suffix", "partial", "accent", "fuzzy", "none")
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
# A target about one of these is a file, not a person ("Ho-so-du-an...docx").
_FILE_CUES = ("file", "tệp", "thư mục", "tài liệu", "folder")


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
    if n == t or "".join(n) == "".join(t):   # a dropped space: "AVinh"
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


# Kinship words, full or abbreviated as in contact names ("A Vinh" = anh Vinh).
_HONORIFICS = frozenset({"a", "anh", "c", "chị", "chi", "e", "em", "cô", "co", "chú", "chu",
                         "bác", "bac", "ông", "ong", "bà", "ba"})
# Capitalised words right after one of these name the person the user means.
_PERSON_MARKERS = frozenset({"cho", "đến", "tới", "với", "và", "anh", "chị", "em", "cô",
                             "chú", "bác", "ông", "bà"})


def is_ui_label(keyword: str) -> bool:
    return _norm(keyword) in _UI_LABELS


def _core_words(words: list) -> list:
    """The name without leading kinship words; never empties it."""
    i = 0
    while i < len(words) - 1 and words[i] in _HONORIFICS:
        i += 1
    return words[i:]


def requested_persons(request: str) -> list:
    """Word lists of the people the request names ("đến anh Vinh" -> [["vinh"]]).
    Empty when none is recognisable, e.g. a request typed all in lowercase."""
    tokens = re.findall(r"\w+", unicodedata.normalize("NFC", request or ""))
    persons, i = [], 0
    while i < len(tokens):
        if tokens[i].casefold() in _PERSON_MARKERS:
            j = i + 1
            while (j < len(tokens) and j - i <= 4 and tokens[j][:1].isupper()
                   and not is_ui_label(tokens[j])):
                j += 1
            core = _core_words([t.casefold() for t in tokens[i + 1:j]])
            if core and core[0] not in _HONORIFICS:
                persons.append(core)
                i = j
                continue
        i += 1
    return persons


def name_matches_request(keyword: str, request: str) -> bool:
    """Is the name the model chose one of the people the user asked for? Accents
    count unless the user typed the whole request without them."""
    persons = requested_persons(request)
    if not persons:
        return True
    typed_with_accents = any(w != _norm(w) for w in _words(request))
    fold = (lambda w: w) if typed_with_accents else _norm
    core = [fold(w) for w in _core_words(_words(keyword))]
    for person in persons:
        wanted = [fold(w) for w in person]
        if _seq_at(wanted, core) >= 0 or _seq_at(core, wanted) >= 0:
            return True
    return False


def is_proper_name(keyword: str, request: str, target: str = "") -> bool:
    """Does this tap keyword name a person/group? Then it needs strict matching.
    True for a non-UI-label keyword that is taken from the user's request and
    capitalised, or that the target describes as a person."""
    keyword = (keyword or "").strip()
    if len(keyword) < 2 or _norm(keyword) in _UI_LABELS or any(c.isdigit() for c in keyword):
        return False
    if any(c in keyword for c in "-_./\\") or any(
            _seq_at(_words(cue), _words(target)) >= 0 for cue in _FILE_CUES):
        return False
    words = _words(keyword)
    capitalised = all(w[:1].isupper() for w in keyword.split())
    request_words = _words(request)
    # Accent-blind on purpose: this only decides whether to apply the strict check,
    # and the user may have typed "truong" for "Trường".
    in_request = (_seq_at(words, request_words) >= 0
                  or _seq_at([_norm(w) for w in _core_words(words)],
                             [_norm(w) for w in request_words]) >= 0)
    target_words = _words(target)
    person_cue = any(_seq_at(_words(cue), target_words) >= 0 for cue in _PERSON_CUES)
    return (capitalised and in_request) or (person_cue and (capitalised or in_request))
