import unittest
import curses
from types import SimpleNamespace

from tg_client import TerminalTelegramTUI


class DummyStdScr:
    def getmaxyx(self):
        return (24, 80)

    def addstr(self, *args, **kwargs):
        return None

    def move(self, *args, **kwargs):
        return None


def make_app() -> TerminalTelegramTUI:
    app = TerminalTelegramTUI(client=object(), stdscr=DummyStdScr())
    app.mode = "chat"
    app.current_dialog = SimpleNamespace(id=100, name="test")
    return app


class ChatKeyBindingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_esc_clears_search_before_leaving_chat(self) -> None:
        app = make_app()
        app.search_query = "hello"
        app.search_match_msg_ids = [1, 2]
        app.search_match_idx = 0
        app.search_focus_msg_id = 1

        await app.handle_chat_key("\x1b")
        self.assertEqual(app.mode, "chat")
        self.assertEqual(app.search_query, "")
        self.assertEqual(app.search_match_msg_ids, [])

        await app.handle_chat_key("\x1b")
        self.assertEqual(app.mode, "dialogs")

    async def test_ctrl_n_moves_search_when_active_and_input_empty(self) -> None:
        app = make_app()
        app.search_query = "hello"
        app.input_buffer = ""
        calls: list[bool] = []
        app._move_search = lambda *, older: calls.append(older)  # type: ignore[assignment]

        await app.handle_chat_key("\x0e")
        self.assertEqual(calls, [True])
        self.assertEqual(app.input_buffer, "")

    async def test_ctrl_p_moves_search_backward_when_active_and_input_empty(self) -> None:
        app = make_app()
        app.search_query = "hello"
        app.input_buffer = ""
        calls: list[bool] = []
        app._move_search = lambda *, older: calls.append(older)  # type: ignore[assignment]

        await app.handle_chat_key("\x10")
        self.assertEqual(calls, [False])

    async def test_ctrl_n_inserts_newline_when_search_not_active(self) -> None:
        app = make_app()
        app.input_buffer = "line"

        await app.handle_chat_key("\x0e")
        self.assertEqual(app.input_buffer, "line\n")
        self.assertEqual(app.draft_by_chat.get(100), "line\n")

    async def test_slash_s_command_triggers_search(self) -> None:
        app = make_app()
        app.input_buffer = "/s keyword"
        app.editing_msg_id = 10
        called: list[str] = []
        app._start_search = lambda query: called.append(query)  # type: ignore[assignment]

        await app.handle_chat_key("\n")
        self.assertEqual(called, ["keyword"])
        self.assertEqual(app.input_buffer, "")
        self.assertIsNone(app.editing_msg_id)
        self.assertIsNone(app.delete_confirm_msg_id)

    async def test_key_up_requests_older_history_when_reaching_top(self) -> None:
        app = make_app()
        app.chat_scroll_offset = 0
        app._chat_max_scroll = lambda: 1  # type: ignore[assignment]
        called: list[bool] = []
        app._request_load_older_history = lambda: called.append(True)  # type: ignore[assignment]

        await app.handle_chat_key(curses.KEY_UP)
        self.assertEqual(app.chat_scroll_offset, 1)
        self.assertEqual(called, [True])

    async def test_key_up_does_not_request_older_history_when_not_at_top(self) -> None:
        app = make_app()
        app.chat_scroll_offset = 0
        app._chat_max_scroll = lambda: 10  # type: ignore[assignment]
        called: list[bool] = []
        app._request_load_older_history = lambda: called.append(True)  # type: ignore[assignment]

        await app.handle_chat_key(curses.KEY_UP)
        self.assertEqual(app.chat_scroll_offset, 1)
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
