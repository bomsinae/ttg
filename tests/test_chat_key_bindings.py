import asyncio
import contextlib
import unittest
import curses
from datetime import datetime
from types import SimpleNamespace

from tg_client import ChatEntry, TerminalTelegramTUI


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


def make_dialog_app(dialog_count: int = 20) -> TerminalTelegramTUI:
    app = TerminalTelegramTUI(client=object(), stdscr=DummyStdScr())
    app.mode = "dialogs"
    app.dialogs = [
        SimpleNamespace(id=idx, name=f"d{idx}", unread_count=0, message=None)
        for idx in range(dialog_count)
    ]
    app.selected_idx = 0
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
        app.input_cursor = len(app.input_buffer)

        await app.handle_chat_key("\x0e")
        self.assertEqual(app.input_buffer, "line\n")
        self.assertEqual(app.input_cursor, len("line\n"))
        self.assertEqual(app.draft_by_chat.get(100), "line\n")

    async def test_slash_s_command_triggers_search(self) -> None:
        app = make_app()
        app.input_buffer = "/s keyword"
        app.input_cursor = len(app.input_buffer)
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

    async def test_page_up_scrolls_by_body_height_and_requests_history_at_top(self) -> None:
        app = make_app()
        app.chat_scroll_offset = 0
        app._chat_body_height = lambda: 6  # type: ignore[assignment]
        app._chat_max_scroll = lambda: 4  # type: ignore[assignment]
        called: list[bool] = []
        app._request_load_older_history = lambda: called.append(True)  # type: ignore[assignment]

        await app.handle_chat_key(curses.KEY_PPAGE)
        self.assertEqual(app.chat_scroll_offset, 4)
        self.assertEqual(called, [True])

    async def test_page_down_scrolls_toward_bottom_by_body_height(self) -> None:
        app = make_app()
        app.chat_scroll_offset = 10
        app._chat_body_height = lambda: 5  # type: ignore[assignment]

        await app.handle_chat_key(curses.KEY_NPAGE)
        self.assertEqual(app.chat_scroll_offset, 6)

    async def test_left_right_moves_cursor_and_inserts_in_middle(self) -> None:
        app = make_app()
        app.input_buffer = "ab"
        app.input_cursor = len(app.input_buffer)

        await app.handle_chat_key(curses.KEY_LEFT)
        await app.handle_chat_key("X")

        self.assertEqual(app.input_buffer, "aXb")
        self.assertEqual(app.input_cursor, 2)

    async def test_backspace_uses_cursor_position(self) -> None:
        app = make_app()
        app.input_buffer = "abcd"
        app.input_cursor = 2

        await app.handle_chat_key(curses.KEY_BACKSPACE)
        self.assertEqual(app.input_buffer, "acd")
        self.assertEqual(app.input_cursor, 1)

    async def test_file_command_dispatches_send_file_action(self) -> None:
        app = make_app()
        app.input_buffer = '/file "/tmp/a b.txt" hello world'
        app.input_cursor = len(app.input_buffer)
        app.editing_msg_id = 12
        app.delete_confirm_msg_id = None
        captured: dict[str, object] = {}

        def fake_send_file(path: str, caption: str = ""):
            captured["path"] = path
            captured["caption"] = caption

            async def _done():
                return None

            return _done()

        def fake_request(coro, *, error_prefix: str):
            captured["error_prefix"] = error_prefix
            coro.close()

        app.send_file_message = fake_send_file  # type: ignore[assignment]
        app._request_message_action = fake_request  # type: ignore[assignment]

        await app.handle_chat_key("\n")
        self.assertEqual(captured.get("path"), "/tmp/a b.txt")
        self.assertEqual(captured.get("caption"), "hello world")
        self.assertEqual(captured.get("error_prefix"), "File send failed")
        self.assertEqual(app.input_buffer, "")
        self.assertIsNone(app.editing_msg_id)
        self.assertIsNone(app.delete_confirm_msg_id)

    async def test_ctrl_w_saves_selected_media_message(self) -> None:
        app = make_app()
        app.chat_entries = [
            ChatEntry(
                sender="me",
                text="<file>",
                when=datetime.now(),
                is_me=True,
                msg_id=88,
                has_media=True,
                is_media=True,
            ),
        ]
        app.editing_msg_id = 88
        captured: dict[str, object] = {}

        def fake_save_media(msg_id: int, output_path: str | None = None):
            captured["msg_id"] = msg_id
            captured["output_path"] = output_path

            async def _done():
                return None

            return _done()

        def fake_request(coro, *, error_prefix: str):
            captured["error_prefix"] = error_prefix
            coro.close()

        app.save_message_media = fake_save_media  # type: ignore[assignment]
        app._request_message_action = fake_request  # type: ignore[assignment]

        await app.handle_chat_key("")  # Ctrl+W
        self.assertEqual(captured.get("msg_id"), 88)
        self.assertIsNone(captured.get("output_path"))
        self.assertEqual(captured.get("error_prefix"), "Media save failed")

    async def test_ctrl_e_selects_outgoing_media_message_too(self) -> None:
        app = make_app()
        app.chat_entries = [
            ChatEntry(
                sender="me",
                text="<file: sample.bin | 10KB>",
                when=datetime.now(),
                is_me=True,
                msg_id=77,
                is_media=True,
                has_media=True,
            ),
        ]

        await app.handle_chat_key("\x05")  # Ctrl+E
        self.assertEqual(app.editing_msg_id, 77)

    async def test_send_current_message_treats_not_modified_as_success(self) -> None:
        app = make_app()
        app.current_dialog = SimpleNamespace(id=100, name="test", entity=object())
        app.chat_entries = [
            ChatEntry(
                sender="me",
                text="same",
                when=datetime.now(),
                is_me=True,
                msg_id=5,
            ),
        ]
        app.editing_msg_id = 5
        app.input_buffer = "same"
        app.input_cursor = len(app.input_buffer)
        app.draft_by_chat[100] = "draft"

        class MessageNotModifiedError(Exception):
            pass

        class DummyClient:
            async def edit_message(self, entity, msg_id, text):
                raise MessageNotModifiedError(
                    "Content of the message was not modified"
                )

        app.client = DummyClient()  # type: ignore[assignment]

        await app.send_current_message()

        self.assertIsNone(app.editing_msg_id)
        self.assertEqual(app.input_buffer, "draft")
        self.assertEqual(app.input_cursor, len("draft"))
        self.assertEqual(app.status, "Message edited")

    async def test_request_message_action_closes_coro_when_busy(self) -> None:
        app = make_app()
        app.message_action_task = asyncio.create_task(asyncio.sleep(1))

        async def dummy_action():
            await asyncio.sleep(0)

        coro = dummy_action()
        app._request_message_action(coro, error_prefix="x")
        self.assertIsNone(coro.cr_frame)
        app.message_action_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await app.message_action_task


class DialogKeyBindingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_page_down_moves_selection_by_dialog_rows(self) -> None:
        app = make_dialog_app()
        app._dialog_rows = lambda: 5  # type: ignore[assignment]
        app.selected_idx = 1

        await app.handle_dialog_key(curses.KEY_NPAGE)
        self.assertEqual(app.selected_idx, 6)

    async def test_page_up_moves_selection_by_dialog_rows(self) -> None:
        app = make_dialog_app()
        app._dialog_rows = lambda: 4  # type: ignore[assignment]
        app.selected_idx = 7

        await app.handle_dialog_key(curses.KEY_PPAGE)
        self.assertEqual(app.selected_idx, 3)

    async def test_page_down_clamps_to_last_dialog(self) -> None:
        app = make_dialog_app(dialog_count=6)
        app._dialog_rows = lambda: 10  # type: ignore[assignment]
        app.selected_idx = 2

        await app.handle_dialog_key(curses.KEY_NPAGE)
        self.assertEqual(app.selected_idx, 5)


if __name__ == "__main__":
    unittest.main()
