"""System prompt and keyword-based skill selection."""
import glob
import os
import re

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
    "từ một ảnh duy nhất. Khi PHÂN VÂN giữa nhiều lựa chọn (nhiều file/người trùng hoặc "
    "gần giống tên, không rõ nền tảng, không tìm thấy thứ cần): gọi ask_user(question, "
    "options) để người dùng chọn rồi làm tiếp, KHÔNG tự đoán. Khi xong hoặc không thể "
    "tiếp tục, trả lời "
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
SKILL_GROUPS = ("common", "windows", "facebook", "zalo", "word")

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
# Whole words only: "password" is not Word, and a bare "doc" is often an unaccented
# "đọc". The generic "tài liệu" counts only when no chat app is in scope, so a chat
# message that mentions a document does not load the Word skills.
WORD_TRIGGER = re.compile(r"\bword\b|\bwinword\b|\bdocx\b|\.doc\b|\bfile doc\b")
WORD_SOFT_TRIGGER = "tài liệu"
# These give generic Windows steps that the word/* skills replace for Word.
WORD_REPLACES = ("windows/open-app", "windows/text-editing")

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
    "zalo/read-chat":     ["đọc", "xem", "tin nhắn mới", "tóm tắt", "có gì", "chưa đọc",
                           "nội dung", "nhắn gì", "nói gì", "hỏi gì", "gửi gì"],

    # word/* only considered when the Word triggers hit (see select_skills)
    "word/overview":    None,   # always, once the group is on
    # Foundational like zalo/open-chat: reading and editing first need the file open.
    "word/open-file":   None,
    # Opening by name goes through Windows Search, so this is needed for most opens.
    "word/find-file":   None,
    "word/close-file":  ["đóng", "tắt", "thoát", "close"],
    "word/read-file":   ["đọc", "xem", "tóm tắt", "nội dung", "có gì", "kiểm tra", "nói về"],
    "word/create-file": ["tạo", "soạn", "viết", "mới", "new"],
    "word/edit-file":   ["sửa", "chỉnh", "thêm", "xoá", "xóa", "thay", "đổi", "định dạng",
                         "in đậm", "in nghiêng", "gạch chân", "chèn", "cập nhật"],
}


SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")


def _load_all_skills(path: str = SKILLS_DIR) -> dict:
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
    word_on = bool(WORD_TRIGGER.search(low)) or (
        WORD_SOFT_TRIGGER in low and not (zalo_on or facebook_on))
    active = {"facebook": facebook_on, "zalo": zalo_on, "word": word_on}   # gated groups
    chosen = []
    for name, kws in SKILL_KEYWORDS.items():
        if name not in ALL_SKILLS or (word_on and name in WORD_REPLACES):
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
