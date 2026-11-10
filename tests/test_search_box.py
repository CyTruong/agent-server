"""Run t6: the model searched "Microsoft Word" first, then the file with ".docx".
Search the file itself, and start every search from an empty box."""
import asyncio

from test_runner import make_runner

FILE = "Ho-so-du-an-bo-mach-may-ban-hang-tu-dong"
T6 = f"Mở tài liệu {FILE} và tóm tắt cho tôi"


def search_runner(monkeypatch, request=T6, groups=("word",)):
    tr = make_runner(monkeypatch)
    tr._task_text = request
    tr._task_groups = groups
    return tr


def run(tr, *calls):
    async def go():
        return [await tr._run_tool("t", name, args) for name, args in calls]
    return asyncio.run(go())


def test_win_s_closes_an_open_search_panel_first(monkeypatch):
    tr = search_runner(monkeypatch, request="mở notepad", groups=())
    run(tr, ("hotkey", {"combo": "win+s"}))
    assert tr.relay.calls == [("key", {"name": "esc"}), ("hotkey", {"combo": "win+s"})]


def test_searching_the_word_app_searches_the_file_instead(monkeypatch):
    tr = search_runner(monkeypatch)
    _, result = run(tr, ("hotkey", {"combo": "win+s"}), ("type", {"text": "Microsoft Word"}))
    assert tr.relay.calls[-2:] == [("hotkey", {"combo": "ctrl+a"}), ("type", {"text": FILE})]
    assert f"gõ '{FILE}' thay cho 'Microsoft Word'" in result
    assert tr._search_query == FILE


def test_extension_is_dropped(monkeypatch):
    tr = search_runner(monkeypatch)
    run(tr, ("hotkey", {"combo": "win+s"}), ("type", {"text": f"{FILE}.docx"}))
    assert tr.relay.calls[-1] == ("type", {"text": FILE})


def test_a_second_search_starts_from_an_empty_box(monkeypatch):
    tr = search_runner(monkeypatch)
    run(tr, ("hotkey", {"combo": "win+s"}), ("type", {"text": "Ho-so"}),
        ("hotkey", {"combo": "win+s"}), ("type", {"text": FILE}))
    assert tr.relay.calls[-4:] == [("key", {"name": "esc"}), ("hotkey", {"combo": "win+s"}),
                                   ("hotkey", {"combo": "ctrl+a"}), ("type", {"text": FILE})]


def test_creating_a_document_may_still_open_word(monkeypatch):
    tr = search_runner(monkeypatch, request="tạo tài liệu word mới về an toàn")
    run(tr, ("hotkey", {"combo": "win+s"}), ("type", {"text": "Word"}))
    assert tr.relay.calls[-1] == ("type", {"text": "Word"})


def test_follow_up_uses_the_file_of_the_previous_request(monkeypatch):
    tr = search_runner(monkeypatch, request="mở lại file đó")
    tr._history = [{"at": __import__("time").monotonic(), "request": T6, "result": "",
                    "groups": ("word",)}]
    run(tr, ("hotkey", {"combo": "win+s"}), ("type", {"text": "Word"}))
    assert tr.relay.calls[-1] == ("type", {"text": FILE})


def test_typing_outside_the_search_box_is_untouched(monkeypatch):
    tr = search_runner(monkeypatch)
    run(tr, ("type", {"text": "Microsoft Word.docx"}))
    assert tr.relay.calls == [("type", {"text": "Microsoft Word.docx"})]
