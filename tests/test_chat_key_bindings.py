import asyncio
import contextlib
import unittest
import curses
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

from tg_client import ChatEntry, TerminalTelegramTUI, display_width, safe_local_time


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
    async def test_media_placeholder_includes_kind_dimensions_size_and_caption(self) -> None:
        app = make_app()
        msg = SimpleNamespace(
            photo=object(),
            video=None,
            voice=None,
            audio=None,
            sticker=None,
            gif=None,
            document=None,
            message="caption text",
            file=SimpleNamespace(name="photo.jpg", size=2048, width=1280, height=720),
            media=object(),
        )

        text, is_media = app._message_text_and_media_flag(msg)  # type: ignore[arg-type]
        self.assertTrue(is_media)
        self.assertIn("[photo - 1280x720 | photo.jpg | 2.0KB]", text)
        self.assertTrue(text.endswith("caption text"))

    async def test_status_suppresses_getdialogs_internal_issue_error(self) -> None:
        app = make_app()
        app._set_status("stable")
        app._set_status(
            "Dialog refresh failed: Telegram is having internal issues RpcCallFailError: "
            "Telegram is having internal issues, please try again later. "
            "(caused by GetDialogsRequest)"
        )
        self.assertEqual(app.status, "stable")

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

    async def test_home_end_move_cursor_to_start_and_end(self) -> None:
        app = make_app()
        app.input_buffer = "abcd"
        app.input_cursor = 2

        await app.handle_chat_key(curses.KEY_HOME)
        self.assertEqual(app.input_cursor, 0)

        await app.handle_chat_key(curses.KEY_END)
        self.assertEqual(app.input_cursor, len("abcd"))

    async def test_backspace_uses_cursor_position(self) -> None:
        app = make_app()
        app.input_buffer = "abcd"
        app.input_cursor = 2

        await app.handle_chat_key(curses.KEY_BACKSPACE)
        self.assertEqual(app.input_buffer, "acd")
        self.assertEqual(app.input_cursor, 1)

    async def test_render_chat_lines_keeps_five_column_side_margin_for_bubbles(self) -> None:
        app = make_app()
        app.chat_entries = [
            ChatEntry(
                sender="other",
                text="x" * 200,
                when=datetime.now(),
                is_me=False,
                msg_id=1,
            ),
            ChatEntry(
                sender="me",
                text="y" * 200,
                when=datetime.now(),
                is_me=True,
                msg_id=2,
            ),
        ]

        width = 60
        lines = app._render_chat_lines(width)
        max_bubble_width = max(
            display_width(line) for line, _, _, msg_id in lines if msg_id is not None
        )
        self.assertLessEqual(max_bubble_width, width - 5)

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

    async def test_p_previews_selected_media_message(self) -> None:
        app = make_app()
        app.chat_entries = [
            ChatEntry(
                sender="other",
                text="<photo>",
                when=datetime.now(),
                is_me=False,
                msg_id=89,
                has_media=True,
                is_media=True,
            ),
        ]
        app.editing_msg_id = 89
        called: list[bool] = []

        async def fake_preview():
            called.append(True)

        app._preview_current_editing = fake_preview  # type: ignore[assignment]

        await app.handle_chat_key("p")
        self.assertEqual(called, [True])

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

    async def test_ctrl_e_can_select_incoming_message(self) -> None:
        app = make_app()
        app.input_buffer = "draft text"
        app.input_cursor = len(app.input_buffer)
        app.chat_entries = [
            ChatEntry(
                sender="me",
                text="mine older",
                when=datetime.now(),
                is_me=True,
                msg_id=10,
            ),
            ChatEntry(
                sender="other",
                text="incoming newer",
                when=datetime.now(),
                is_me=False,
                msg_id=11,
            ),
        ]

        await app.handle_chat_key("\x05")  # Ctrl+E
        self.assertEqual(app.editing_msg_id, 11)
        self.assertEqual(app.status, "Selected message (read-only)")
        self.assertEqual(app.input_buffer, "draft text")

    async def test_enter_on_incoming_selection_returns_to_input_mode(self) -> None:
        app = make_app()
        app.chat_entries = [
            ChatEntry(
                sender="other",
                text="incoming",
                when=datetime.now(),
                is_me=False,
                msg_id=51,
            ),
        ]
        app.editing_msg_id = 51
        app.draft_by_chat[100] = "draft text"
        app.input_buffer = "incoming"
        app.input_cursor = len(app.input_buffer)
        called: list[bool] = []
        app._request_send_current = lambda: called.append(True)  # type: ignore[assignment]

        await app.handle_chat_key("\n")
        self.assertIsNone(app.editing_msg_id)
        self.assertEqual(app.input_buffer, "draft text")
        self.assertEqual(app.input_cursor, len("draft text"))
        self.assertEqual(called, [])
        self.assertEqual(app.status, "Input mode")

    async def test_ctrl_w_saves_selected_incoming_media_message(self) -> None:
        app = make_app()
        app.chat_entries = [
            ChatEntry(
                sender="other",
                text="<photo: a.jpg>",
                when=datetime.now(),
                is_me=False,
                msg_id=91,
                has_media=True,
                is_media=True,
            ),
        ]
        app.editing_msg_id = 91
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

        await app.handle_chat_key("\x17")  # Ctrl+W
        self.assertEqual(captured.get("msg_id"), 91)
        self.assertIsNone(captured.get("output_path"))
        self.assertEqual(captured.get("error_prefix"), "Media save failed")

    async def test_send_current_message_rejects_incoming_message_edit(self) -> None:
        app = make_app()
        app.chat_entries = [
            ChatEntry(
                sender="other",
                text="incoming",
                when=datetime.now(),
                is_me=False,
                msg_id=22,
            ),
        ]
        app.editing_msg_id = 22
        app.input_buffer = "try edit"
        app.input_cursor = len(app.input_buffer)

        class DummyClient:
            def __init__(self) -> None:
                self.called = False

            async def edit_message(self, entity, msg_id, text):
                self.called = True
                return None

        dummy_client = DummyClient()
        app.client = dummy_client  # type: ignore[assignment]

        await app.send_current_message()
        self.assertFalse(dummy_client.called)
        self.assertEqual(app.status, "Only your messages can be edited.")
        self.assertEqual(app.editing_msg_id, 22)
        self.assertEqual(app.input_buffer, "try edit")

    async def test_ctrl_d_rejects_incoming_message_delete(self) -> None:
        app = make_app()
        app.chat_entries = [
            ChatEntry(
                sender="other",
                text="incoming",
                when=datetime.now(),
                is_me=False,
                msg_id=33,
            ),
        ]
        app.editing_msg_id = 33

        await app.handle_chat_key("\x04")  # Ctrl+D
        self.assertIsNone(app.delete_confirm_msg_id)
        self.assertEqual(app.status, "Only your messages can be deleted.")

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

    async def test_on_new_message_appends_outgoing_from_other_device(self) -> None:
        app = make_app()
        app.mode = "chat"
        app.current_dialog = SimpleNamespace(id=100, name="test")
        app.chat_entries = [
            ChatEntry(
                sender="me",
                text="existing",
                when=datetime.now(),
                is_me=True,
                msg_id=10,
            )
        ]

        class DummyEvent:
            chat_id = 100
            id = 11
            message = SimpleNamespace(
                id=11,
                out=True,
                message="from other device",
                date=datetime.now(),
                media=None,
                sender_id=None,
                sender=None,
            )

            async def get_sender(self):
                return None

            async def get_chat(self):
                return None

        await app.on_new_message(DummyEvent())
        self.assertEqual(len(app.chat_entries), 2)
        self.assertTrue(app.chat_entries[-1].is_me)
        self.assertEqual(app.chat_entries[-1].text, "from other device")

    async def test_on_new_message_ignores_duplicate_msg_id(self) -> None:
        app = make_app()
        app.mode = "chat"
        app.current_dialog = SimpleNamespace(id=100, name="test")
        app.chat_entries = [
            ChatEntry(
                sender="me",
                text="existing",
                when=datetime.now(),
                is_me=True,
                msg_id=11,
            )
        ]

        class DummyEvent:
            chat_id = 100
            id = 11
            message = SimpleNamespace(
                id=11,
                out=True,
                message="duplicate",
                date=datetime.now(),
                media=None,
                sender_id=None,
                sender=None,
            )

            async def get_sender(self):
                return None

            async def get_chat(self):
                return None

        await app.on_new_message(DummyEvent())
        self.assertEqual(len(app.chat_entries), 1)

    async def test_on_message_read_updates_outbox_receipts_for_current_chat(self) -> None:
        app = make_app()
        app.mode = "chat"
        app.current_dialog = SimpleNamespace(id=100, name="test")
        app.chat_entries = [
            ChatEntry(
                sender="me",
                text="hello",
                when=datetime.now(),
                is_me=True,
                msg_id=21,
                read=False,
            ),
            ChatEntry(
                sender="other",
                text="reply",
                when=datetime.now(),
                is_me=False,
                msg_id=22,
                read=False,
            ),
        ]

        event = SimpleNamespace(chat_id=100, max_id=21, outbox=True, inbox=False)
        await app.on_message_read(event)  # type: ignore[arg-type]

        self.assertEqual(app.read_outbox_max_by_chat.get(100), 21)
        self.assertTrue(app.chat_entries[0].read)
        self.assertFalse(app.chat_entries[1].read)
        self.assertTrue(app.dialog_refresh_requested)

    async def test_on_message_read_ignores_inbox_reads(self) -> None:
        app = make_app()
        app.mode = "chat"
        app.current_dialog = SimpleNamespace(id=100, name="test")
        app.chat_entries = [
            ChatEntry(
                sender="me",
                text="hello",
                when=datetime.now(),
                is_me=True,
                msg_id=30,
                read=False,
            ),
        ]

        event = SimpleNamespace(chat_id=100, max_id=30, outbox=False, inbox=True)
        await app.on_message_read(event)  # type: ignore[arg-type]

        self.assertEqual(app.read_outbox_max_by_chat.get(100), None)
        self.assertFalse(app.chat_entries[0].read)

    async def test_on_user_update_sets_typing_status_for_current_chat(self) -> None:
        app = make_app()
        app.mode = "chat"
        app.current_dialog = SimpleNamespace(id=100, name="test", is_group=False, is_channel=False, entity=None)
        event = SimpleNamespace(
            chat_id=None,
            user_id=100,
            typing=True,
            recording=False,
            audio=False,
            uploading=False,
            photo=False,
            video=False,
            document=False,
            playing=False,
            cancel=False,
            user=None,
        )

        await app.on_user_update(event)  # type: ignore[arg-type]
        self.assertEqual(app._current_peer_status_text(), "typing...")

    async def test_on_user_update_in_group_includes_actor_name(self) -> None:
        app = make_app()
        app.mode = "chat"
        app.current_dialog = SimpleNamespace(
            id=100,
            name="group",
            is_group=True,
            is_channel=False,
            entity=None,
        )
        event = SimpleNamespace(
            chat_id=100,
            typing=True,
            recording=False,
            audio=False,
            uploading=False,
            photo=False,
            video=False,
            document=False,
            playing=False,
            cancel=False,
            user=SimpleNamespace(first_name="Alice", last_name="", username="", self=False),
        )

        await app.on_user_update(event)  # type: ignore[arg-type]
        self.assertEqual(app._current_peer_status_text(), "Alice typing...")

    async def test_incoming_message_clears_typing_status(self) -> None:
        app = make_app()
        app.mode = "chat"
        app.current_dialog = SimpleNamespace(id=100, name="test")
        app._set_peer_action("typing...")

        class DummyEvent:
            chat_id = 100
            id = 50
            message = SimpleNamespace(
                id=50,
                out=False,
                message="hello",
                date=datetime.now(),
                media=None,
                sender_id=None,
                sender=None,
            )

            async def get_sender(self):
                return None

            async def get_chat(self):
                return None

        await app.on_new_message(DummyEvent())
        self.assertEqual(app.peer_action_text, "")

    async def test_on_new_message_other_chat_outgoing_does_not_raise_unread_badge(self) -> None:
        app = make_app()
        app.mode = "chat"
        app.current_dialog = SimpleNamespace(id=100, name="test")

        class DummyEvent:
            chat_id = 200
            id = 40
            message = SimpleNamespace(
                id=40,
                out=True,
                message="sent elsewhere",
                date=datetime.now(),
                media=None,
                sender_id=None,
                sender=None,
            )

            async def get_sender(self):
                return None

            async def get_chat(self):
                return None

        await app.on_new_message(DummyEvent())
        self.assertEqual(app.other_chat_new_counts.get(200), None)

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

    async def test_dialog_last_message_time_is_right_side_value_source(self) -> None:
        app = make_dialog_app(dialog_count=0)
        ts = datetime.now().astimezone().replace(hour=9, minute=5, second=0, microsecond=0)
        dialog = SimpleNamespace(message=SimpleNamespace(date=ts))

        self.assertEqual(
            app._dialog_last_message_time(dialog),
            safe_local_time(ts).strftime("%H:%M"),
        )

    async def test_dialog_last_message_time_non_today_uses_date_and_weekday(self) -> None:
        app = make_dialog_app(dialog_count=0)
        ts = (datetime.now().astimezone() - timedelta(days=1)).replace(
            hour=9,
            minute=5,
            second=0,
            microsecond=0,
        )
        dialog = SimpleNamespace(message=SimpleNamespace(date=ts))

        self.assertEqual(
            app._dialog_last_message_time(dialog),
            safe_local_time(ts).strftime("%m-%d (%a)"),
        )

    async def test_dialog_preview_includes_read_receipt_for_outgoing_last_message(self) -> None:
        app = make_dialog_app(dialog_count=0)
        dialog = SimpleNamespace(
            id=100,
            message=SimpleNamespace(
                id=10,
                out=True,
                message="hello",
                media=None,
            ),
        )
        app.read_outbox_max_by_chat[100] = 10

        preview = app._dialog_preview(dialog, 80)
        self.assertIn("✓✓", preview)
        self.assertIn("hello", preview)

    async def test_dialog_preview_includes_single_check_for_unread_outgoing_last_message(self) -> None:
        app = make_dialog_app(dialog_count=0)
        dialog = SimpleNamespace(
            id=100,
            message=SimpleNamespace(
                id=11,
                out=True,
                message="hello",
                media=None,
            ),
        )
        app.read_outbox_max_by_chat[100] = 10

        preview = app._dialog_preview(dialog, 80)
        self.assertIn("✓", preview)
        self.assertNotIn("✓✓", preview)

    async def test_dialog_preview_includes_sender_name_for_group_chat(self) -> None:
        app = make_dialog_app(dialog_count=0)
        dialog = SimpleNamespace(
            id=100,
            is_group=True,
            is_channel=False,
            entity=SimpleNamespace(megagroup=False),
            message=SimpleNamespace(
                id=12,
                out=False,
                message="hello",
                media=None,
                sender=SimpleNamespace(first_name="Alice", last_name="", username=""),
                sender_id=55,
            ),
        )

        preview = app._dialog_preview(dialog, 80)
        self.assertIn("Alice:", preview)
        self.assertIn("hello", preview)

    async def test_dialog_preview_does_not_include_sender_name_for_direct_chat(self) -> None:
        app = make_dialog_app(dialog_count=0)
        dialog = SimpleNamespace(
            id=100,
            is_group=False,
            is_channel=False,
            entity=SimpleNamespace(megagroup=False),
            message=SimpleNamespace(
                id=13,
                out=False,
                message="hello",
                media=None,
                sender=SimpleNamespace(first_name="Alice", last_name="", username=""),
                sender_id=55,
            ),
        )

        preview = app._dialog_preview(dialog, 80)
        self.assertNotIn("Alice:", preview)
        self.assertEqual(preview, "hello")

    async def test_selected_dialog_id_uses_cursor_in_dialog_mode(self) -> None:
        app = make_dialog_app(dialog_count=3)
        app.mode = "dialogs"
        app.current_dialog = SimpleNamespace(id=0, name="stale")
        app.selected_idx = 2

        self.assertEqual(app._selected_dialog_id(), 2)

    async def test_refresh_dialogs_retries_and_keeps_running_on_transient_errors(self) -> None:
        app = make_dialog_app(dialog_count=1)
        app._set_status("keep")

        class RpcCallFailError(Exception):
            pass

        class DummyClient:
            def __init__(self) -> None:
                self.calls = 0

            def iter_dialogs(self, limit=120):
                self.calls += 1

                async def _gen():
                    raise RpcCallFailError(
                        "Telegram is having internal issues, please try again later."
                    )
                    yield  # pragma: no cover

                return _gen()

        client = DummyClient()
        app.client = client  # type: ignore[assignment]
        app._dialog_refresh_retry_delay = lambda exc, attempt: 0.0  # type: ignore[assignment]
        before_ids = [dialog.id for dialog in app.dialogs]

        await app.refresh_dialogs()

        after_ids = [dialog.id for dialog in app.dialogs]
        self.assertEqual(after_ids, before_ids)
        self.assertEqual(client.calls, 3)
        self.assertTrue(app.dialog_refresh_requested)
        self.assertEqual(app.status, "keep")
        self.assertGreater(app.dialog_refresh_backoff_until, time.monotonic())
        self.assertEqual(app.dialog_refresh_failures, 1)

    async def test_request_refresh_respects_backoff_unless_forced(self) -> None:
        app = make_dialog_app(dialog_count=1)
        app.dialog_refresh_backoff_until = time.monotonic() + 60.0
        calls: list[str] = []

        class DummyTask:
            def done(self) -> bool:
                return False

        def fake_start_task(coro, *, error_prefix: str, on_done=None):
            calls.append(error_prefix)
            coro.close()
            return DummyTask()

        app._start_task = fake_start_task  # type: ignore[assignment]

        app._request_refresh(quiet=True)
        self.assertEqual(calls, [])

        app._request_refresh(quiet=True, force=True)
        self.assertEqual(calls, ["Dialog refresh failed"])

    async def test_refresh_dialogs_raises_non_transient_error(self) -> None:
        app = make_dialog_app(dialog_count=1)

        class DummyClient:
            def iter_dialogs(self, limit=120):
                async def _gen():
                    raise RuntimeError("hard failure")
                    yield  # pragma: no cover

                return _gen()

        app.client = DummyClient()  # type: ignore[assignment]

        with self.assertRaises(RuntimeError):
            await app.refresh_dialogs()


if __name__ == "__main__":
    unittest.main()
