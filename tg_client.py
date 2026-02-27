#!/usr/bin/env python3
"""
Terminal Telegram third-party client (MTProto) with a simple TUI.

Key flow:
- Launch -> dialog list appears first
- Arrow up/down to move
- Enter to open selected dialog
- In chat view, type and Enter to send
- Esc to return to dialog list
"""

from __future__ import annotations

import asyncio
import curses
import json
import locale
import logging
import os
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.custom import Dialog, Message


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def control_char(letter: str) -> str:
    ch = (letter or "n")[0].lower()
    if not ("a" <= ch <= "z"):
        ch = "n"
    return chr(ord(ch) & 0x1F)


def parse_key_binding(raw: Any, default: str) -> str:
    if not isinstance(raw, str):
        return default
    text = raw.strip()
    if not text:
        return default
    lower = text.lower()
    if len(text) == 1:
        return text
    if lower.startswith("ctrl+") and len(lower) == 6 and lower[5].isalpha():
        return control_char(lower[5])
    aliases = {
        "enter": "\n",
        "esc": "\x1b",
        "escape": "\x1b",
        "tab": "\t",
    }
    return aliases.get(lower, default)


def _as_positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


def _as_positive_float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed <= 0:
        return default
    return parsed


@dataclass
class AppConfig:
    auto_refresh_interval_sec: float = 8.0
    peer_status_refresh_interval_sec: float = 15.0
    peer_status_typing_refresh_interval_sec: float = 3.0
    initial_message_limit: int = 80
    history_batch_size: int = 80
    key_newline: str = "ctrl+n"
    key_search_prev: str = "ctrl+p"
    key_edit_older: str = "ctrl+e"
    key_edit_newer: str = "ctrl+r"
    key_cancel_edit: str = "ctrl+g"
    key_delete_selected: str = "ctrl+d"
    log_file: str = "logs/ttg.log"
    log_level: str = "INFO"


def load_app_config(path: Path | None = None) -> AppConfig:
    config = AppConfig()
    config_path = path or Path(os.getenv("TTG_CONFIG_PATH", "ttg_config.json"))
    if not config_path.exists():
        return config

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return config
    if not isinstance(payload, dict):
        return config

    timers = payload.get("timers", {})
    if not isinstance(timers, dict):
        timers = {}
    history = payload.get("history", {})
    if not isinstance(history, dict):
        history = {}
    keys = payload.get("keys", {})
    if not isinstance(keys, dict):
        keys = {}
    logging_cfg = payload.get("logging", {})
    if not isinstance(logging_cfg, dict):
        logging_cfg = {}

    config.auto_refresh_interval_sec = _as_positive_float(
        timers.get("auto_refresh_interval_sec", config.auto_refresh_interval_sec),
        config.auto_refresh_interval_sec,
    )
    config.peer_status_refresh_interval_sec = _as_positive_float(
        timers.get(
            "peer_status_refresh_interval_sec",
            config.peer_status_refresh_interval_sec,
        ),
        config.peer_status_refresh_interval_sec,
    )
    config.peer_status_typing_refresh_interval_sec = _as_positive_float(
        timers.get(
            "peer_status_typing_refresh_interval_sec",
            config.peer_status_typing_refresh_interval_sec,
        ),
        config.peer_status_typing_refresh_interval_sec,
    )
    config.initial_message_limit = _as_positive_int(
        history.get("initial_message_limit", config.initial_message_limit),
        config.initial_message_limit,
    )
    config.history_batch_size = _as_positive_int(
        history.get("batch_size", config.history_batch_size),
        config.history_batch_size,
    )

    if isinstance(keys.get("newline"), str):
        config.key_newline = keys["newline"]
    if isinstance(keys.get("search_prev"), str):
        config.key_search_prev = keys["search_prev"]
    if isinstance(keys.get("edit_older"), str):
        config.key_edit_older = keys["edit_older"]
    if isinstance(keys.get("edit_newer"), str):
        config.key_edit_newer = keys["edit_newer"]
    if isinstance(keys.get("cancel_edit"), str):
        config.key_cancel_edit = keys["cancel_edit"]
    if isinstance(keys.get("delete_selected"), str):
        config.key_delete_selected = keys["delete_selected"]

    if isinstance(logging_cfg.get("file"), str) and logging_cfg.get("file").strip():
        config.log_file = logging_cfg.get("file").strip()
    if isinstance(logging_cfg.get("level"), str) and logging_cfg.get("level").strip():
        config.log_level = logging_cfg.get("level").strip().upper()

    return config


def setup_logging(config: AppConfig) -> logging.Logger:
    logger = logging.getLogger("ttg")
    logger.handlers.clear()
    logger.propagate = False
    level = getattr(logging, config.log_level.upper(), logging.INFO)
    logger.setLevel(level)

    handler: logging.Handler
    fallback_to_stderr = False
    try:
        log_path = Path(config.log_file)
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except Exception:
        handler = logging.StreamHandler(sys.stderr)
        fallback_to_stderr = True
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logger.addHandler(handler)
    if fallback_to_stderr:
        logger.warning(
            "Failed to initialize log file '%s'; using stderr logging",
            config.log_file,
        )
    return logger


def entity_label(entity: Any, fallback: str = "unknown") -> str:
    if entity is None:
        return fallback

    title = getattr(entity, "title", None)
    if title:
        return str(title)

    first = getattr(entity, "first_name", "") or ""
    last = getattr(entity, "last_name", "") or ""
    username = getattr(entity, "username", "") or ""

    name = " ".join(part for part in [first, last] if part).strip()
    if username:
        return f"{name} (@{username})" if name else f"@{username}"
    if name:
        return name
    return fallback


def message_text(text: str | None) -> str:
    if text is None or text == "":
        return "<media>"
    return str(text)


def safe_local_time(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.now()
    try:
        return dt.astimezone()
    except ValueError:
        return dt


def char_width(ch: str) -> int:
    if not ch:
        return 0
    if ch == "\t":
        return 4
    if unicodedata.combining(ch):
        return 0
    category = unicodedata.category(ch)
    if category.startswith("C"):
        return 0
    if unicodedata.east_asian_width(ch) in {"W", "F"}:
        return 2
    return 1


def display_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


def clip_to_width(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    out: list[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if w > 0 and used + w > max_width:
            break
        out.append(ch)
        used += w
    return "".join(out)


def pad_to_width(text: str, target_width: int) -> str:
    clipped = clip_to_width(text, target_width)
    pad = max(0, target_width - display_width(clipped))
    return clipped + (" " * pad)


def ellipsize(text: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if display_width(text) <= max_width:
        return text
    if max_width <= 3:
        return clip_to_width(text, max_width)
    return clip_to_width(text, max_width - 3) + "..."


def wrap_by_width(text: str, max_width: int) -> list[str]:
    if max_width <= 0:
        return [""]
    if not text:
        return [""]

    chunks: list[str] = []
    cur: list[str] = []
    used = 0
    for ch in text:
        w = char_width(ch)
        if w > 0 and used + w > max_width and cur:
            chunks.append("".join(cur))
            cur = [ch]
            used = w
            continue
        if w > 0 and used + w > max_width:
            chunks.append("")
            cur = [ch]
            used = w
            continue
        cur.append(ch)
        used += w
    if cur or not chunks:
        chunks.append("".join(cur))
    return chunks


@dataclass
class ChatEntry:
    sender: str
    text: str
    when: datetime
    is_me: bool = False
    msg_id: int | None = None
    read: bool = False


class TerminalTelegramTUI:
    def __init__(
        self,
        client: TelegramClient,
        stdscr: Any,
        *,
        config: AppConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.client = client
        self.stdscr = stdscr
        self.config = config or AppConfig()
        self.logger = logger or logging.getLogger("ttg")
        if logger is None:
            if not self.logger.handlers:
                self.logger.addHandler(logging.NullHandler())
            self.logger.propagate = False

        self.running = True
        self.mode = "dialogs"  # "dialogs" | "chat"

        self.dialogs: list[Dialog] = []
        self.selected_idx = 0
        self.dialog_top = 0
        self.current_dialog: Dialog | None = None

        self.chat_entries: list[ChatEntry] = []
        self.chat_scroll_offset = 0
        self.input_buffer = ""

        self.status = "Connecting..."
        self.needs_redraw = True

        self.dialog_refresh_requested = False
        self.last_dialog_refresh = 0.0
        self.info_bar_attr = curses.A_REVERSE
        self.badge_unread_attr = curses.A_BOLD
        self.badge_muted_attr = curses.A_DIM
        self.read_outbox_max_by_chat: dict[int, int] = {}
        self.refresh_task: asyncio.Task[Any] | None = None
        self.open_task: asyncio.Task[Any] | None = None
        self.message_action_task: asyncio.Task[Any] | None = None
        self.peer_status_task: asyncio.Task[Any] | None = None
        self.history_task: asyncio.Task[Any] | None = None
        self.ack_tasks_by_chat: dict[int, asyncio.Task[Any]] = {}
        self.ack_pending_max_by_chat: dict[int, int] = {}
        self._all_tasks: set[asyncio.Task[Any]] = set()
        self.auto_refresh_interval = self.config.auto_refresh_interval_sec
        self.peer_status_refresh_interval = self.config.peer_status_refresh_interval_sec
        self.peer_status_refresh_typing_interval = (
            self.config.peer_status_typing_refresh_interval_sec
        )
        self.peer_status_last_refresh = 0.0
        self.peer_status_text = "Status unavailable"
        self.editing_msg_id: int | None = None
        self.delete_confirm_msg_id: int | None = None
        self.draft_by_chat: dict[int, str] = {}
        self.search_query = ""
        self.search_match_msg_ids: list[int] = []
        self.search_match_idx = -1
        self.search_focus_msg_id: int | None = None
        self.other_chat_new_counts: dict[int, int] = {}
        self.other_chat_names: dict[int, str] = {}
        self.oldest_loaded_msg_id_by_chat: dict[int, int] = {}
        self.history_exhausted_by_chat: dict[int, bool] = {}
        self.history_batch_size = self.config.history_batch_size
        self.initial_message_limit = self.config.initial_message_limit
        self.key_newline = parse_key_binding(
            self.config.key_newline,
            control_char("n"),
        )
        self.key_search_prev = parse_key_binding(
            self.config.key_search_prev,
            control_char("p"),
        )
        self.key_edit_older = parse_key_binding(
            self.config.key_edit_older,
            control_char("e"),
        )
        self.key_edit_newer = parse_key_binding(
            self.config.key_edit_newer,
            control_char("r"),
        )
        self.key_cancel_edit = parse_key_binding(
            self.config.key_cancel_edit,
            control_char("g"),
        )
        self.key_delete_selected = parse_key_binding(
            self.config.key_delete_selected,
            control_char("d"),
        )

        self._init_colors()

    def _init_colors(self) -> None:
        try:
            if not curses.has_colors():
                return
            curses.start_color()
            try:
                curses.use_default_colors()
            except curses.error:
                pass

            try:
                curses.init_pair(2, curses.COLOR_WHITE, curses.COLOR_BLACK)
                self.info_bar_attr = curses.color_pair(2)
            except curses.error:
                self.info_bar_attr = curses.A_REVERSE
            try:
                curses.init_pair(3, curses.COLOR_BLUE, -1)
                self.badge_unread_attr = curses.color_pair(3) | curses.A_BOLD
            except curses.error:
                self.badge_unread_attr = curses.A_BOLD
            try:
                curses.init_pair(4, curses.COLOR_WHITE, -1)
                self.badge_muted_attr = curses.color_pair(4) | curses.A_DIM
            except curses.error:
                self.badge_muted_attr = curses.A_DIM
        except curses.error:
            self.info_bar_attr = curses.A_REVERSE
            self.badge_unread_attr = curses.A_BOLD
            self.badge_muted_attr = curses.A_DIM

    def _set_status(self, value: str) -> None:
        self.status = value.replace("\n", " ").strip()
        self.needs_redraw = True

    def _start_task(
        self,
        coro: Any,
        *,
        error_prefix: str,
        on_done: Any | None = None,
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._all_tasks.add(task)

        def _done(t: asyncio.Task[Any]) -> None:
            self._all_tasks.discard(t)
            try:
                t.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover
                self.logger.exception("%s", error_prefix)
                self._set_status(f"{error_prefix}: {exc}")
            if on_done is not None:
                on_done()
            self.needs_redraw = True

        task.add_done_callback(_done)
        return task

    def _request_refresh(self, quiet: bool = False) -> None:
        if self.refresh_task is not None and not self.refresh_task.done():
            return

        def _clear() -> None:
            self.refresh_task = None

        self.refresh_task = self._start_task(
            self.refresh_dialogs(quiet=quiet),
            error_prefix="Dialog refresh failed",
            on_done=_clear,
        )

    def _request_open_selected(self) -> None:
        if self.open_task is not None and not self.open_task.done():
            return

        def _clear() -> None:
            self.open_task = None

        self.open_task = self._start_task(
            self.open_selected_dialog(),
            error_prefix="Open failed",
            on_done=_clear,
        )

    def _request_message_action(self, coro: Any, *, error_prefix: str) -> None:
        if self.message_action_task is not None and not self.message_action_task.done():
            self._set_status("Previous message action still in progress...")
            return

        def _clear() -> None:
            self.message_action_task = None

        self.message_action_task = self._start_task(
            coro,
            error_prefix=error_prefix,
            on_done=_clear,
        )

    def _request_send_current(self) -> None:
        self._request_message_action(
            self.send_current_message(),
            error_prefix="Send failed",
        )

    def _request_peer_status_refresh(self, force: bool = False) -> None:
        if self.peer_status_task is not None and not self.peer_status_task.done():
            return

        def _clear() -> None:
            self.peer_status_task = None

        self.peer_status_task = self._start_task(
            self.refresh_peer_status(force=force),
            error_prefix="Peer status failed",
            on_done=_clear,
        )

    def _request_load_older_history(self) -> None:
        if self.history_task is not None and not self.history_task.done():
            return
        if self.current_dialog is None or self.mode != "chat":
            return

        dialog = self.current_dialog
        chat_id = dialog.id
        if self.history_exhausted_by_chat.get(chat_id, False):
            return

        oldest_id = self.oldest_loaded_msg_id_by_chat.get(chat_id)
        if oldest_id is None or oldest_id <= 1:
            self.history_exhausted_by_chat[chat_id] = True
            return

        def _clear() -> None:
            self.history_task = None

        self.history_task = self._start_task(
            self.load_older_history(dialog, before_id=oldest_id),
            error_prefix="History load failed",
            on_done=_clear,
        )

    @staticmethod
    def _format_user_presence(status: Any) -> str:
        if status is None:
            return "Status unavailable"

        name = status.__class__.__name__
        if name == "UserStatusOnline":
            return "Online"
        if name == "UserStatusOffline":
            was_online = getattr(status, "was_online", None)
            if isinstance(was_online, datetime):
                when = safe_local_time(was_online).strftime("%m-%d %H:%M")
                return f"Last seen {when}"
            return "Offline"
        if name == "UserStatusRecently":
            return "Last seen recently"
        if name == "UserStatusLastWeek":
            return "Last seen within a week"
        if name == "UserStatusLastMonth":
            return "Last seen within a month"
        return "Status unavailable"

    def _format_peer_status(self, entity: Any) -> str:
        if entity is None:
            return "Status unavailable"
        if getattr(entity, "bot", False):
            return "Bot account"

        if hasattr(entity, "status"):
            return self._format_user_presence(getattr(entity, "status", None))

        participants = getattr(entity, "participants_count", None)
        if isinstance(participants, int):
            return f"Members {participants}"

        if getattr(entity, "broadcast", False):
            return "Channel"
        if getattr(entity, "megagroup", False):
            return "Group"
        return "Chat"

    async def refresh_peer_status(self, force: bool = False) -> None:
        if self.current_dialog is None or self.mode != "chat":
            return

        now = time.monotonic()
        interval = (
            self.peer_status_refresh_typing_interval
            if self.input_buffer
            else self.peer_status_refresh_interval
        )
        if not force and now - self.peer_status_last_refresh < interval:
            return

        dialog = self.current_dialog
        entity = await self.client.get_entity(dialog.entity)
        if self.current_dialog is None or self.current_dialog.id != dialog.id:
            return

        self.peer_status_text = self._format_peer_status(entity)
        self.peer_status_last_refresh = now
        self.needs_redraw = True

    def _schedule_ack_read(self, dialog: Dialog | None, max_id: int | None = None) -> None:
        if dialog is None:
            return

        chat_id = dialog.id
        if isinstance(max_id, int) and max_id > 0:
            prev = self.ack_pending_max_by_chat.get(chat_id, 0)
            if max_id > prev:
                self.ack_pending_max_by_chat[chat_id] = max_id
        elif chat_id not in self.ack_pending_max_by_chat:
            self.ack_pending_max_by_chat[chat_id] = 0

        existing = self.ack_tasks_by_chat.get(chat_id)
        if existing is not None and not existing.done():
            return

        async def _job() -> None:
            while True:
                target = self.ack_pending_max_by_chat.get(chat_id, 0)
                await self._ack_read(
                    dialog,
                    max_id=target if isinstance(target, int) and target > 0 else None,
                    quiet=True,
                )
                latest = self.ack_pending_max_by_chat.get(chat_id, 0)
                if not isinstance(latest, int) or latest <= target:
                    break
            self.ack_pending_max_by_chat.pop(chat_id, None)

        def _clear() -> None:
            self.ack_tasks_by_chat.pop(chat_id, None)

        self.ack_tasks_by_chat[chat_id] = self._start_task(
            _job(),
            error_prefix="Read sync failed",
            on_done=_clear,
        )

    def _set_dialog_unread_local(self, chat_id: int, unread_count: int) -> None:
        for dialog in self.dialogs:
            if dialog.id != chat_id:
                continue
            try:
                dialog.unread_count = unread_count
            except Exception:
                pass
            break

        if self.current_dialog is not None and self.current_dialog.id == chat_id:
            try:
                self.current_dialog.unread_count = unread_count
            except Exception:
                pass

        self.needs_redraw = True

    def _editable_outgoing_entries(self) -> list[ChatEntry]:
        entries: list[ChatEntry] = []
        for entry in reversed(self.chat_entries):
            if not entry.is_me or entry.msg_id is None:
                continue
            if entry.text == "<media>":
                continue
            entries.append(entry)
        return entries

    def _cycle_edit_outgoing(self, *, older: bool) -> None:
        if self.current_dialog is None:
            self._set_status("No active dialog.")
            return

        editable_entries = self._editable_outgoing_entries()
        if not editable_entries:
            self._set_status("No editable sent message found.")
            return

        if self.editing_msg_id is None:
            self._sync_current_draft()

        target_idx = 0
        if self.editing_msg_id is not None:
            current_idx = -1
            for idx, entry in enumerate(editable_entries):
                if entry.msg_id == self.editing_msg_id:
                    current_idx = idx
                    break

            if current_idx >= 0:
                if older:
                    if current_idx + 1 >= len(editable_entries):
                        current = editable_entries[current_idx]
                        self.input_buffer = current.text
                        self.chat_scroll_offset = 0
                        self.needs_redraw = True
                        self._set_status(
                            f"Already at oldest editable message #{current.msg_id}"
                        )
                        return
                    target_idx = current_idx + 1
                else:
                    if current_idx == 0:
                        current = editable_entries[current_idx]
                        self.input_buffer = current.text
                        self.chat_scroll_offset = 0
                        self.needs_redraw = True
                        self._set_status(
                            f"Already at newest editable message #{current.msg_id}"
                        )
                        return
                    target_idx = current_idx - 1

        target = editable_entries[target_idx]

        self.editing_msg_id = target.msg_id
        self.input_buffer = target.text
        self._ensure_selected_message_visible()
        self.needs_redraw = True
        self._set_status(f"Editing message #{target.msg_id}")

    def _cancel_edit_mode(self, *, clear_input: bool = False) -> None:
        if self.editing_msg_id is None:
            return
        self.editing_msg_id = None
        self.delete_confirm_msg_id = None
        if clear_input:
            if self.current_dialog is not None:
                self.input_buffer = self.draft_by_chat.get(self.current_dialog.id, "")
            else:
                self.input_buffer = ""
        self.needs_redraw = True
        self._set_status("Edit mode canceled")

    def _entry_by_id(self, msg_id: int) -> ChatEntry | None:
        for entry in self.chat_entries:
            if entry.msg_id == msg_id:
                return entry
        return None

    def _sync_current_draft(self) -> None:
        if self.current_dialog is None or self.editing_msg_id is not None:
            return
        chat_id = self.current_dialog.id
        if self.input_buffer:
            self.draft_by_chat[chat_id] = self.input_buffer
        else:
            self.draft_by_chat.pop(chat_id, None)

    def _clear_search_state(self) -> None:
        self.search_query = ""
        self.search_match_msg_ids = []
        self.search_match_idx = -1
        self.search_focus_msg_id = None

    def _rebuild_search_matches(self, *, preserve_focus: bool) -> None:
        query = self.search_query.strip()
        if not query:
            self._clear_search_state()
            return

        needle = query.casefold()
        matches = [
            entry.msg_id
            for entry in self.chat_entries
            if entry.msg_id is not None and needle in entry.text.casefold()
        ]
        prev_focus = self.search_focus_msg_id
        prev_idx = self.search_match_idx
        self.search_match_msg_ids = matches
        if not matches:
            self.search_match_idx = -1
            self.search_focus_msg_id = None
            return

        if preserve_focus and prev_focus in matches:
            idx = matches.index(prev_focus)
        elif preserve_focus and prev_idx >= 0:
            idx = min(prev_idx, len(matches) - 1)
        else:
            idx = len(matches) - 1

        self.search_match_idx = idx
        self.search_focus_msg_id = matches[idx]

    def _start_search(self, raw_query: str) -> None:
        query = raw_query.strip()
        if not query:
            self._set_status("Usage: /s <query>")
            return

        self.search_query = query
        self._rebuild_search_matches(preserve_focus=False)
        if self.search_focus_msg_id is None:
            self.needs_redraw = True
            self._set_status(f"No match for '{query}'")
            return

        self._ensure_message_visible(self.search_focus_msg_id)
        self.needs_redraw = True
        self._set_status(
            f"Search '{query}' {self.search_match_idx + 1}/{len(self.search_match_msg_ids)}"
        )

    def _move_search(self, *, older: bool) -> None:
        if not self.search_query or not self.search_match_msg_ids:
            self._set_status("No active search. Use /s <query>.")
            return

        step = -1 if older else 1
        next_idx = self.search_match_idx + step
        if next_idx < 0 or next_idx >= len(self.search_match_msg_ids):
            edge = "oldest" if older else "newest"
            self._set_status(f"Already at {edge} search result")
            return

        self.search_match_idx = next_idx
        self.search_focus_msg_id = self.search_match_msg_ids[next_idx]
        self._ensure_message_visible(self.search_focus_msg_id)
        self.needs_redraw = True
        self._set_status(
            f"Search '{self.search_query}' {self.search_match_idx + 1}/{len(self.search_match_msg_ids)}"
        )

    def _ensure_selected_message_visible(self) -> None:
        self._ensure_message_visible(self.editing_msg_id)

    def _chat_body_height(self) -> int:
        height, _ = self.stdscr.getmaxyx()
        return max(1, height - 4)

    def _chat_max_scroll(self) -> int:
        _, width = self.stdscr.getmaxyx()
        lines = self._render_chat_lines(max(1, width - 1))
        return max(0, len(lines) - self._chat_body_height())

    def _ensure_message_visible(self, target_msg_id: int | None) -> None:
        if target_msg_id is None:
            return

        _, width = self.stdscr.getmaxyx()
        body_height = self._chat_body_height()
        lines = self._render_chat_lines(max(1, width - 1))
        if not lines:
            self.chat_scroll_offset = 0
            return

        target_indices = [
            idx
            for idx, (_, _, _, msg_id) in enumerate(lines)
            if msg_id == target_msg_id
        ]
        if not target_indices:
            return

        target_start = target_indices[0]
        target_end = target_indices[-1] + 1
        total = len(lines)
        max_scroll = max(0, total - body_height)
        if self.chat_scroll_offset > max_scroll:
            self.chat_scroll_offset = max_scroll
        if self.chat_scroll_offset < 0:
            self.chat_scroll_offset = 0

        end = total - self.chat_scroll_offset
        start = max(0, end - body_height)
        if target_start >= start and target_end <= end:
            return

        new_end = target_end
        new_start = max(0, new_end - body_height)
        if target_start < new_start:
            new_start = target_start
            new_end = min(total, new_start + body_height)
        new_offset = total - new_end
        if new_offset < 0:
            new_offset = 0
        if new_offset > max_scroll:
            new_offset = max_scroll
        self.chat_scroll_offset = new_offset

    def _request_delete_current_editing(self) -> None:
        if self.current_dialog is None:
            self._set_status("No active dialog.")
            return

        if self.editing_msg_id is None:
            self._set_status("Select a sent message first (Ctrl+E/Ctrl+R).")
            return

        entry = self._entry_by_id(self.editing_msg_id)
        if entry is None or not entry.is_me:
            self._set_status("Selected message is unavailable.")
            return

        self.delete_confirm_msg_id = self.editing_msg_id
        self.needs_redraw = True
        self._set_status(f"Confirm delete #{self.editing_msg_id} (Enter/Y or Esc/N)")

    def _confirm_delete_current_editing(self) -> None:
        target_id = self.delete_confirm_msg_id
        if target_id is None:
            return
        self.delete_confirm_msg_id = None
        self._request_message_action(
            self.delete_outgoing_message(target_id),
            error_prefix="Delete failed",
        )

    def _cancel_delete_confirm(self) -> None:
        if self.delete_confirm_msg_id is None:
            return
        self.delete_confirm_msg_id = None
        self.needs_redraw = True
        self._set_status("Delete canceled")

    async def _ack_read(
        self, dialog: Dialog | None, max_id: int | None = None, quiet: bool = True
    ) -> None:
        if dialog is None:
            return

        kwargs: dict[str, Any] = {"clear_mentions": True}
        if isinstance(max_id, int) and max_id > 0:
            kwargs["max_id"] = max_id

        try:
            await self.client.send_read_acknowledge(dialog.entity, **kwargs)
        except Exception as exc:  # pragma: no cover
            if quiet:
                self.logger.debug("Read sync failed chat=%s: %s", dialog.id, exc)
            else:
                self.logger.warning("Read sync failed chat=%s: %s", dialog.id, exc)
            if not quiet:
                self._set_status(f"Read sync failed: {exc}")
            return

        self._set_dialog_unread_local(dialog.id, 0)
        self.dialog_refresh_requested = True

    @staticmethod
    def _dialog_read_outbox_max(dialog: Dialog) -> int:
        direct = getattr(dialog, "read_outbox_max_id", None)
        if isinstance(direct, int):
            return direct
        raw = getattr(dialog, "dialog", None)
        raw_value = getattr(raw, "read_outbox_max_id", None)
        if isinstance(raw_value, int):
            return raw_value
        return 0

    def _apply_read_receipts(self, chat_id: int) -> None:
        read_max = self.read_outbox_max_by_chat.get(chat_id, 0)
        changed = False
        for entry in self.chat_entries:
            if not entry.is_me or entry.msg_id is None or entry.read:
                continue
            if entry.msg_id <= read_max:
                entry.read = True
                changed = True
        if changed:
            self.needs_redraw = True

    def _selected_dialog_id(self) -> int | None:
        if self.current_dialog is not None:
            return self.current_dialog.id
        if 0 <= self.selected_idx < len(self.dialogs):
            return self.dialogs[self.selected_idx].id
        return None

    def _other_chat_alert_text(self) -> str:
        if not self.other_chat_new_counts:
            return ""
        total_new = sum(
            count for count in self.other_chat_new_counts.values() if isinstance(count, int)
        )
        if total_new <= 0:
            return ""
        chat_count = len(self.other_chat_new_counts)
        if chat_count == 1:
            chat_id = next(iter(self.other_chat_new_counts))
            name = self.other_chat_names.get(chat_id, f"id:{chat_id}")
            return f"Other: {name} +{total_new}"
        return f"Other chats: {chat_count} (+{total_new})"

    def _dialog_rows(self) -> int:
        height, _ = self.stdscr.getmaxyx()
        return max(1, (height - 2) // 3)

    def _is_dialog_muted(self, dialog: Dialog) -> bool:
        notify = getattr(getattr(dialog, "dialog", None), "notify_settings", None)
        if notify is None:
            notify = getattr(getattr(dialog, "entity", None), "notify_settings", None)
        if notify is None:
            return False

        # Some peers may expose "silent" instead of an active mute_until window.
        if getattr(notify, "silent", False) is True:
            return True

        mute_until = getattr(notify, "mute_until", None)
        if isinstance(mute_until, datetime):
            try:
                if mute_until.tzinfo is None:
                    return mute_until > datetime.now()
                return mute_until > datetime.now(mute_until.tzinfo)
            except Exception:
                return False

        if isinstance(mute_until, bool) or not isinstance(mute_until, int):
            return False
        if mute_until <= 0:
            return False
        if mute_until >= 2_000_000_000:
            return True
        return mute_until > int(time.time())

    def _ensure_dialog_visible(self) -> None:
        rows = self._dialog_rows()
        if self.selected_idx < self.dialog_top:
            self.dialog_top = self.selected_idx
        if self.selected_idx >= self.dialog_top + rows:
            self.dialog_top = self.selected_idx - rows + 1
        if self.dialog_top < 0:
            self.dialog_top = 0

    async def refresh_dialogs(self, limit: int = 120, quiet: bool = False) -> None:
        preserve_id = self._selected_dialog_id()
        dialogs: list[Dialog] = []
        async for dialog in self.client.iter_dialogs(limit=limit):
            dialogs.append(dialog)

        self.dialogs = dialogs
        for dialog in dialogs:
            chat_id = dialog.id
            read_max = self._dialog_read_outbox_max(dialog)
            prev = self.read_outbox_max_by_chat.get(chat_id, 0)
            if read_max > prev:
                self.read_outbox_max_by_chat[chat_id] = read_max
            elif chat_id not in self.read_outbox_max_by_chat:
                self.read_outbox_max_by_chat[chat_id] = prev

            if dialog.unread_count > 0:
                self.other_chat_names[chat_id] = dialog.name.replace("\n", " ")
            else:
                self.other_chat_new_counts.pop(chat_id, None)
                self.other_chat_names.pop(chat_id, None)

        if not self.dialogs:
            self.selected_idx = 0
            self.dialog_top = 0
        else:
            new_idx = self.selected_idx
            if preserve_id is not None:
                for idx, dialog in enumerate(self.dialogs):
                    if dialog.id == preserve_id:
                        new_idx = idx
                        break

            if new_idx < 0:
                new_idx = 0
            if new_idx >= len(self.dialogs):
                new_idx = len(self.dialogs) - 1
            self.selected_idx = new_idx
            self._ensure_dialog_visible()

        if self.current_dialog is not None:
            for dialog in self.dialogs:
                if dialog.id == self.current_dialog.id:
                    self.current_dialog = dialog
                    break
            self._apply_read_receipts(self.current_dialog.id)
            self.other_chat_new_counts.pop(self.current_dialog.id, None)
            self.other_chat_names.pop(self.current_dialog.id, None)

        self.last_dialog_refresh = time.monotonic()
        self.dialog_refresh_requested = False
        if not quiet:
            self._set_status(f"Loaded {len(self.dialogs)} dialogs")

    def _entry_from_message(self, msg: Message, chat_id: int | None = None) -> ChatEntry:
        msg_id = msg.id if isinstance(msg.id, int) else None
        is_me = bool(msg.out)
        if msg.out:
            sender = "me"
        else:
            sender = entity_label(
                getattr(msg, "sender", None),
                fallback=f"id:{msg.sender_id}" if msg.sender_id is not None else "unknown",
            )
        read = False
        if is_me and msg_id is not None:
            resolved_chat_id = chat_id
            if resolved_chat_id is None and self.current_dialog is not None:
                resolved_chat_id = self.current_dialog.id
            read_max = self.read_outbox_max_by_chat.get(resolved_chat_id or 0, 0)
            read = msg_id <= read_max
        return ChatEntry(
            sender=sender,
            text=message_text(msg.message),
            when=safe_local_time(msg.date),
            is_me=is_me,
            msg_id=msg_id,
            read=read,
        )

    async def load_older_history(
        self, dialog: Dialog, *, before_id: int, limit: int | None = None
    ) -> None:
        if before_id <= 1:
            self.history_exhausted_by_chat[dialog.id] = True
            return

        batch = limit or self.history_batch_size
        if batch <= 0:
            return

        fetch_max_id = before_id - 1
        messages = await self.client.get_messages(
            dialog.entity,
            limit=batch,
            max_id=fetch_max_id,
        )
        if not messages:
            self.history_exhausted_by_chat[dialog.id] = True
            return

        msg_ids = [msg.id for msg in messages if isinstance(msg.id, int)]
        if msg_ids:
            new_oldest = min(msg_ids)
            self.oldest_loaded_msg_id_by_chat[dialog.id] = new_oldest
            if new_oldest <= 1:
                self.history_exhausted_by_chat[dialog.id] = True

        if len(messages) < batch:
            self.history_exhausted_by_chat[dialog.id] = True

        if self.current_dialog is None or self.current_dialog.id != dialog.id:
            return

        older_entries = [
            self._entry_from_message(msg, chat_id=dialog.id) for msg in reversed(messages)
        ]
        if not older_entries:
            return

        self.chat_entries = older_entries + self.chat_entries
        self._rebuild_search_matches(preserve_focus=True)
        self.needs_redraw = True

    async def open_selected_dialog(self) -> None:
        if not self.dialogs:
            self._set_status("No dialogs available. Press r to refresh.")
            return

        if not (0 <= self.selected_idx < len(self.dialogs)):
            self._set_status("Invalid selection.")
            return

        dialog = self.dialogs[self.selected_idx]
        if self.history_task is not None and not self.history_task.done():
            self.history_task.cancel()
            self.history_task = None
        self.current_dialog = dialog
        self.other_chat_new_counts.pop(dialog.id, None)
        self.other_chat_names.pop(dialog.id, None)
        self.mode = "chat"
        self.input_buffer = self.draft_by_chat.get(dialog.id, "")
        self.editing_msg_id = None
        self.delete_confirm_msg_id = None
        self._clear_search_state()
        self.chat_scroll_offset = 0
        self.chat_entries = []
        self.peer_status_text = "Loading peer status..."
        self.peer_status_last_refresh = 0.0
        self._set_status(f"Opening {dialog.name} ...")

        messages = await self.client.get_messages(
            dialog.entity,
            limit=self.initial_message_limit,
        )
        self.chat_entries = [
            self._entry_from_message(msg, chat_id=dialog.id) for msg in reversed(messages)
        ]
        msg_ids = [msg.id for msg in messages if isinstance(msg.id, int)]
        if msg_ids:
            oldest_id = min(msg_ids)
            self.oldest_loaded_msg_id_by_chat[dialog.id] = oldest_id
            self.history_exhausted_by_chat[dialog.id] = (
                len(messages) < self.history_batch_size or oldest_id <= 1
            )
        else:
            self.oldest_loaded_msg_id_by_chat.pop(dialog.id, None)
            self.history_exhausted_by_chat[dialog.id] = True
        self._apply_read_receipts(dialog.id)
        newest_id = max(
            (msg.id for msg in messages if isinstance(msg.id, int)),
            default=None,
        )
        self._schedule_ack_read(dialog, max_id=newest_id)
        self._request_peer_status_refresh(force=True)
        self._set_status(
            f"{dialog.name} ({dialog.id}) | Enter: send | Ctrl+N: newline | Esc: dialogs"
        )

    async def send_current_message(self) -> None:
        if self.current_dialog is None:
            self._set_status("No active dialog.")
            return

        raw_text = self.input_buffer
        if not raw_text.strip():
            return

        dialog = self.current_dialog
        edit_msg_id = self.editing_msg_id
        self.input_buffer = ""
        text = raw_text
        if edit_msg_id is not None:
            self._set_status(f"Editing #{edit_msg_id}...")
            try:
                edited = await self.client.edit_message(dialog.entity, edit_msg_id, text)
            except Exception as exc:  # pragma: no cover
                self.logger.warning(
                    "Edit failed chat=%s msg=%s: %s",
                    dialog.id,
                    edit_msg_id,
                    exc,
                )
                self.input_buffer = raw_text
                self._set_status(f"Edit failed: {exc}")
                return

            if self.current_dialog is None or self.current_dialog.id != dialog.id:
                return

            self.editing_msg_id = None
            self.input_buffer = self.draft_by_chat.get(dialog.id, "")
            replaced = False
            for entry in self.chat_entries:
                if entry.msg_id != edit_msg_id:
                    continue
                entry.text = message_text(getattr(edited, "message", text))
                entry.when = safe_local_time(getattr(edited, "date", entry.when))
                replaced = True
                break
            if not replaced:
                self.chat_entries.append(
                    self._entry_from_message(edited, chat_id=dialog.id)
                )

            self._rebuild_search_matches(preserve_focus=True)
            self.chat_scroll_offset = 0
            self.dialog_refresh_requested = True
            self._set_status(f"Edited #{edit_msg_id}")
            return

        self._sync_current_draft()
        self._set_status("Sending...")
        try:
            sent = await self.client.send_message(dialog.entity, text)
        except Exception as exc:  # pragma: no cover
            self.logger.warning("Send failed chat=%s: %s", dialog.id, exc)
            self.draft_by_chat[dialog.id] = raw_text
            if self.current_dialog is not None and self.current_dialog.id == dialog.id:
                self.input_buffer = raw_text
            self._set_status(f"Send failed: {exc}")
            return

        self.draft_by_chat.pop(dialog.id, None)
        if self.current_dialog is None or self.current_dialog.id != dialog.id:
            return
        self.chat_entries.append(
            self._entry_from_message(sent, chat_id=dialog.id)
        )
        self._rebuild_search_matches(preserve_focus=True)
        self.chat_scroll_offset = 0
        self.dialog_refresh_requested = True
        self._set_status("Sent")

    async def delete_outgoing_message(self, target_id: int) -> None:
        if self.current_dialog is None:
            self._set_status("No active dialog.")
            return

        entry = self._entry_by_id(target_id)
        if entry is None or not entry.is_me:
            self._set_status("Selected message is unavailable.")
            return

        dialog = self.current_dialog
        self._set_status(f"Deleting #{target_id}...")
        try:
            await self.client.delete_messages(dialog.entity, [target_id], revoke=True)
        except Exception as exc:  # pragma: no cover
            self.logger.warning(
                "Delete failed chat=%s msg=%s: %s",
                dialog.id,
                target_id,
                exc,
            )
            self._set_status(f"Delete failed: {exc}")
            return

        if self.current_dialog is None or self.current_dialog.id != dialog.id:
            return

        self.chat_entries = [
            item for item in self.chat_entries if item.msg_id != target_id
        ]
        if self.editing_msg_id == target_id:
            self.editing_msg_id = None
            self.input_buffer = self.draft_by_chat.get(dialog.id, "")
        self._rebuild_search_matches(preserve_focus=True)
        self.chat_scroll_offset = 0
        self.dialog_refresh_requested = True
        self._set_status(f"Deleted #{target_id}")

    async def on_new_message(self, event: events.NewMessage.Event) -> None:
        self.dialog_refresh_requested = True
        if (
            self.mode == "chat"
            and self.current_dialog is not None
            and event.chat_id == self.current_dialog.id
        ):
            sender = entity_label(
                await event.get_sender(),
                fallback=f"id:{event.sender_id}" if event.sender_id is not None else "unknown",
            )
            self.chat_entries.append(
                ChatEntry(
                    sender=sender,
                    text=message_text(event.raw_text),
                    when=safe_local_time(event.date),
                    is_me=False,
                    msg_id=event.id if isinstance(event.id, int) else None,
                    read=False,
                )
            )
            self._rebuild_search_matches(preserve_focus=True)
            self._schedule_ack_read(self.current_dialog, max_id=event.id)
            self._set_status(
                f"{self.current_dialog.name} ({self.current_dialog.id}) | Enter: send | Ctrl+N: newline | Esc: dialogs"
            )
        else:
            chat_id = event.chat_id if isinstance(event.chat_id, int) else None
            if chat_id is not None:
                self.other_chat_new_counts[chat_id] = (
                    self.other_chat_new_counts.get(chat_id, 0) + 1
                )
                if chat_id not in self.other_chat_names:
                    dialog_name = None
                    for dialog in self.dialogs:
                        if dialog.id == chat_id:
                            dialog_name = dialog.name
                            break
                    if dialog_name is None:
                        try:
                            dialog_name = entity_label(
                                await event.get_chat(),
                                fallback=f"id:{chat_id}",
                            )
                        except Exception:
                            dialog_name = f"id:{chat_id}"
                    self.other_chat_names[chat_id] = dialog_name.replace("\n", " ")
            self.needs_redraw = True
            self._set_status("New incoming message. Press r to refresh dialogs now.")

    async def handle_dialog_key(self, key: Any) -> None:
        if key == curses.KEY_DOWN:
            if self.selected_idx < len(self.dialogs) - 1:
                self.selected_idx += 1
                self._ensure_dialog_visible()
                self.needs_redraw = True
            return

        if key == curses.KEY_UP:
            if self.selected_idx > 0:
                self.selected_idx -= 1
                self._ensure_dialog_visible()
                self.needs_redraw = True
            return

        if key in ("\n", "\r") or key == curses.KEY_ENTER:
            self._request_open_selected()
            return

        if key in ("\x1b", 27, "\x03"):
            self.running = False
            return

        if key in ("r", "R"):
            self._request_refresh(quiet=False)
            return

    async def handle_chat_key(self, key: Any) -> None:
        if self.delete_confirm_msg_id is not None:
            if key in ("\r", "\n", 13, 10) or key == curses.KEY_ENTER:
                self._confirm_delete_current_editing()
                return
            if key in ("y", "Y"):
                self._confirm_delete_current_editing()
                return
            if key in ("\x1b", 27, "n", "N"):
                self._cancel_delete_confirm()
                return
            return

        if key in ("\x1b", 27):
            if self.search_query and self.editing_msg_id is None:
                self._clear_search_state()
                self.needs_redraw = True
                self._set_status("Search cleared. Press Esc again for dialogs.")
                return
            if self.history_task is not None and not self.history_task.done():
                self.history_task.cancel()
                self.history_task = None
            self._sync_current_draft()
            self.mode = "dialogs"
            self.input_buffer = ""
            self.editing_msg_id = None
            self.delete_confirm_msg_id = None
            self._set_status("Dialog list | Up/Down move | Enter open | Esc quit")
            return

        if key == curses.KEY_UP:
            self.chat_scroll_offset += 1
            if self.chat_scroll_offset >= self._chat_max_scroll():
                self._request_load_older_history()
            self.needs_redraw = True
            return

        if key == curses.KEY_DOWN:
            if self.chat_scroll_offset > 0:
                self.chat_scroll_offset -= 1
            self.needs_redraw = True
            return

        if key == self.key_newline:
            if (
                self.search_query
                and self.editing_msg_id is None
                and not self.input_buffer
            ):
                self._move_search(older=True)
                return
            self.input_buffer += "\n"
            self._sync_current_draft()
            self.needs_redraw = True
            self._request_peer_status_refresh(force=False)
            return

        if key == self.key_search_prev:
            if (
                self.search_query
                and self.editing_msg_id is None
                and not self.input_buffer
            ):
                self._move_search(older=False)
            return

        if key == self.key_edit_older:
            self._cycle_edit_outgoing(older=True)
            return

        if key == self.key_edit_newer:
            self._cycle_edit_outgoing(older=False)
            return

        if key == self.key_cancel_edit:
            self._cancel_edit_mode(clear_input=True)
            return

        if key == self.key_delete_selected:
            self._request_delete_current_editing()
            return

        if key in ("\r", "\n", 13, 10) or key == curses.KEY_ENTER:
            if self.chat_scroll_offset > 0:
                self.chat_scroll_offset = 0
                self.needs_redraw = True
                return

            cmd = self.input_buffer.strip()
            if cmd in ("/s", "/search"):
                self.input_buffer = ""
                self._sync_current_draft()
                self._start_search("")
                return
            if cmd.startswith("/s ") or cmd.startswith("/search "):
                if cmd.startswith("/s "):
                    query = cmd[len("/s ") :]
                else:
                    query = cmd[len("/search ") :]
                self.input_buffer = ""
                self._sync_current_draft()
                if self.editing_msg_id is not None:
                    self.editing_msg_id = None
                    self.delete_confirm_msg_id = None
                self._start_search(query)
                return
            if cmd in ("/clearsearch", "/searchclear"):
                self.input_buffer = ""
                self._sync_current_draft()
                self._clear_search_state()
                self.needs_redraw = True
                self._set_status("Search cleared")
                return

            if cmd in (
                "/edit",
                "/edit_last",
                "/editlast",
                "/older",
            ):
                self.input_buffer = ""
                self._sync_current_draft()
                self._cycle_edit_outgoing(older=True)
                return
            if cmd in ("/newer",):
                self.input_buffer = ""
                self._sync_current_draft()
                self._cycle_edit_outgoing(older=False)
                return
            if cmd in (
                "/delete",
                "/del",
                "/delete_last",
                "/deletelast",
            ):
                self.input_buffer = ""
                self._sync_current_draft()
                self._request_delete_current_editing()
                self.needs_redraw = True
                return
            if cmd in ("/cancel", "/cancel_edit", "/canceledit"):
                self.input_buffer = ""
                self._sync_current_draft()
                self._cancel_edit_mode(clear_input=True)
                return
            self._request_send_current()
            return

        if key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
            if self.input_buffer:
                self.input_buffer = self.input_buffer[:-1]
                self._sync_current_draft()
                self.needs_redraw = True
            return

        if key == "\x15":  # Ctrl+U
            self.input_buffer = ""
            self._sync_current_draft()
            self.needs_redraw = True
            return

        if key == "\x03":
            self.running = False
            return

        if isinstance(key, str) and key.isprintable():
            self.input_buffer += key
            self._sync_current_draft()
            self.needs_redraw = True
            self._request_peer_status_refresh(force=False)

    async def handle_input(self) -> None:
        while True:
            try:
                key = self.stdscr.get_wch()
            except curses.error:
                break

            if key == curses.KEY_RESIZE:
                self.needs_redraw = True
                continue

            if self.mode == "dialogs":
                await self.handle_dialog_key(key)
            else:
                await self.handle_chat_key(key)

            if not self.running:
                return

    def _write(self, y: int, x: int, text: str, attr: int = 0) -> None:
        height, width = self.stdscr.getmaxyx()
        if y < 0 or y >= height or x >= width:
            return
        available = width - x - 1
        if available <= 0:
            return

        clipped = clip_to_width(text, available)
        try:
            self.stdscr.addstr(y, x, clipped, attr)
        except curses.error:
            pass

    def _dialog_preview(self, dialog: Dialog, max_width: int) -> str:
        if max_width <= 0:
            return ""

        text = ""
        if dialog.message is not None:
            text = message_text(getattr(dialog.message, "message", None))
        text = text.replace("\n", " ")
        return ellipsize(text, max_width)

    def _box_top_line(
        self,
        inner_width: int,
        header: str,
        right_aligned: bool,
        *,
        emphasized: bool = False,
    ) -> str:
        span = inner_width + 2
        label = clip_to_width(f" {header} ", span)
        fill = max(0, span - display_width(label))
        left = "┏" if emphasized else "┌"
        right = "┓" if emphasized else "┐"
        bar = "━" if emphasized else "─"
        if right_aligned:
            return left + (bar * fill) + label + right
        return left + label + (bar * fill) + right

    def draw_dialogs(self) -> None:
        height, width = self.stdscr.getmaxyx()
        self._write(
            0,
            1,
            "Dialogs | Up/Down: move | Enter: open | r: refresh | Esc: quit",
            curses.A_BOLD,
        )
        self._write(1, 0, "─" * max(0, width - 1), curses.A_DIM)

        rows = self._dialog_rows()
        self._ensure_dialog_visible()

        if not self.dialogs:
            self._write(2, 0, "No dialogs. Press r to refresh.")
        else:
            badge_col_width = 4
            for dialog in self.dialogs:
                unread_count = getattr(dialog, "unread_count", 0)
                if unread_count > 0:
                    badge_col_width = max(
                        badge_col_width, display_width(f"[{unread_count}] ")
                    )

            for row in range(rows):
                idx = self.dialog_top + row
                if idx >= len(self.dialogs):
                    break

                y = 2 + (row * 3)
                if y >= height:
                    break

                dialog = self.dialogs[idx]
                name = dialog.name.replace("\n", " ")
                unread = dialog.unread_count
                badge = f"[{unread}] " if unread > 0 else ""
                badge_pad = " " * max(0, badge_col_width - display_width(badge))
                is_selected = idx == self.selected_idx

                badge_text = f"{badge}{badge_pad}"
                badge_width = display_width(badge_text)

                msg_prefix = f"{' ' * badge_col_width}"
                preview_width = max(0, width - display_width(msg_prefix) - 1)
                preview = self._dialog_preview(dialog, preview_width)
                msg_line = f"{msg_prefix}{preview}" if preview else msg_prefix

                name_attr = curses.A_BOLD
                msg_attr = curses.A_DIM
                if is_selected:
                    name_attr |= curses.A_REVERSE
                    msg_attr = curses.A_REVERSE
                    row_fill = " " * max(0, width - 1)
                    self._write(y, 0, row_fill, curses.A_REVERSE)
                    if y + 1 < height:
                        self._write(y + 1, 0, row_fill, curses.A_REVERSE)

                badge_attr = name_attr
                if unread > 0:
                    if self._is_dialog_muted(dialog):
                        badge_attr = self.badge_muted_attr
                    else:
                        badge_attr = self.badge_unread_attr

                self._write(y, 0, badge_text, badge_attr)
                self._write(y, badge_width, name, name_attr)
                if y + 1 < height:
                    self._write(y + 1, 0, msg_line, msg_attr)
                if y + 2 < height:
                    divider = "─" * max(0, width - 1)
                    div_attr = curses.A_DIM
                    self._write(y + 2, 0, divider, div_attr)

    def _render_chat_lines(self, width: int) -> list[tuple[str, bool, bool, int | None]]:
        if width <= 0:
            return []

        rendered: list[tuple[str, bool, bool, int | None]] = []
        inner_max = max(1, width - 4)
        for idx, entry in enumerate(self.chat_entries):
            if idx > 0:
                rendered.append(("", False, False, None))

            stamp = entry.when.strftime("%H:%M")
            lines = entry.text.splitlines() or [""]
            message_lines: list[str] = []

            for line in lines:
                chunks = wrap_by_width(line, inner_max)
                for chunk in chunks:
                    message_lines.append(clip_to_width(chunk, inner_max))

            if not message_lines:
                message_lines = [""]

            sender_label = clip_to_width(entry.sender, inner_max)
            header_label = f"{sender_label} ({stamp})"
            if entry.is_me:
                receipt = "✓✓" if entry.read else "✓"
                header_label = f"{header_label} {receipt}"
            is_edit_selected = (
                self.editing_msg_id is not None and entry.msg_id == self.editing_msg_id
            )
            is_search_focus = (
                self.editing_msg_id is None
                and self.search_focus_msg_id is not None
                and entry.msg_id == self.search_focus_msg_id
            )
            if is_search_focus:
                header_label = f"{header_label} [FIND]"
            header_label = clip_to_width(header_label, inner_max)

            inner_width = max(
                1,
                display_width(header_label),
                *(display_width(line) for line in message_lines),
            )
            is_selected = is_edit_selected or is_search_focus
            vertical = "┃" if is_edit_selected else "│"
            bottom_left = "┗" if is_edit_selected else "└"
            bottom_right = "┛" if is_edit_selected else "┘"
            bottom_bar = "━" if is_edit_selected else "─"
            rendered.append(
                (
                    self._box_top_line(
                        inner_width,
                        header_label,
                        right_aligned=entry.is_me,
                        emphasized=is_edit_selected,
                    ),
                    entry.is_me,
                    is_selected,
                    entry.msg_id,
                )
            )
            for line in message_lines:
                rendered.append(
                    (
                        f"{vertical} {pad_to_width(line, inner_width)} {vertical}",
                        entry.is_me,
                        is_selected,
                        entry.msg_id,
                    )
                )
            rendered.append(
                (
                    bottom_left + (bottom_bar * (inner_width + 2)) + bottom_right,
                    entry.is_me,
                    is_selected,
                    entry.msg_id,
                )
            )

        return rendered

    def _render_input_lines(
        self, width: int, rows: int
    ) -> tuple[list[tuple[str, str]], int]:
        prompt = "E> " if self.editing_msg_id is not None else "> "
        continuation = "  "
        prompt_w = display_width(prompt)
        usable_w = max(1, width - prompt_w - 1)

        logical_lines = self.input_buffer.split("\n")
        visual_lines: list[str] = []
        for logical in logical_lines:
            visual_lines.extend(wrap_by_width(logical, usable_w))
        if not visual_lines:
            visual_lines = [""]

        visible = visual_lines[-rows:]
        top_padding = rows - len(visible)
        rendered: list[tuple[str, str]] = []
        for row_idx in range(rows):
            if row_idx < top_padding:
                rendered.append((continuation, ""))
                continue
            content_idx = row_idx - top_padding
            prefix = prompt if content_idx == 0 else continuation
            rendered.append((prefix, visible[content_idx]))

        cursor_prefix, cursor_line = rendered[-1]
        cursor_x = min(max(0, width - 2), display_width(cursor_prefix) + display_width(cursor_line))
        return rendered, cursor_x

    def _draw_delete_confirm_modal(self) -> None:
        target_id = self.delete_confirm_msg_id
        if target_id is None:
            return

        height, width = self.stdscr.getmaxyx()
        if width < 20 or height < 8:
            return

        preview = ""
        entry = self._entry_by_id(target_id)
        if entry is not None:
            preview = entry.text.replace("\n", " ")

        max_inner = max(1, width - 8)
        title = "Delete selected message?"
        details = f"#{target_id}: {preview}" if preview else f"#{target_id}"
        hint = "Enter/Y: delete   Esc/N: cancel"
        lines = [
            ellipsize(title, max_inner),
            ellipsize(details, max_inner),
            ellipsize(hint, max_inner),
        ]

        inner_width = max(display_width(line) for line in lines)
        box_width = inner_width + 4
        box_height = len(lines) + 2
        available_w = max(1, width - 1)
        x = max(0, (available_w - box_width) // 2)
        y = max(1, (height - box_height) // 2)

        self._write(y, x, "┌" + ("─" * (inner_width + 2)) + "┐", curses.A_REVERSE)
        for idx, line in enumerate(lines):
            content = f"│ {pad_to_width(line, inner_width)} │"
            self._write(y + 1 + idx, x, content, curses.A_REVERSE)
        self._write(
            y + box_height - 1,
            x,
            "└" + ("─" * (inner_width + 2)) + "┘",
            curses.A_REVERSE,
        )

    def draw_chat(self) -> None:
        height, width = self.stdscr.getmaxyx()
        if self.current_dialog is None:
            self.mode = "dialogs"
            self._set_status("Dialog closed.")
            self.draw_dialogs()
            return

        chat_name = str(self.current_dialog.name).replace("\n", " ").strip()
        title = f"{chat_name} | {self.peer_status_text}"
        other_alert = self._other_chat_alert_text()
        if other_alert:
            title = f"{title} | {other_alert}"
        title = clip_to_width(title, max(1, width - 2))
        top_fill = " " * max(0, width - 1)
        self._write(0, 0, top_fill, self.info_bar_attr)
        self._write(0, 1, title, self.info_bar_attr | curses.A_BOLD)

        body_top = 1
        row_cursor = max(body_top, height - 1)

        input_rows: list[int] = []
        for _ in range(2):
            if row_cursor >= body_top:
                input_rows.insert(0, row_cursor)
                row_cursor -= 1

        info_bar_row: int | None = None
        if row_cursor >= body_top:
            info_bar_row = row_cursor
            row_cursor -= 1

        body_height = max(1, row_cursor - body_top + 1)

        lines = self._render_chat_lines(width - 1)
        max_scroll = max(0, len(lines) - body_height)
        if self.chat_scroll_offset > max_scroll:
            self.chat_scroll_offset = max_scroll
        if self.chat_scroll_offset < 0:
            self.chat_scroll_offset = 0
        end = len(lines) - self.chat_scroll_offset
        start = max(0, end - body_height)
        visible = lines[start:end]
        for idx, (line, is_me, is_selected, _) in enumerate(visible):
            attr = curses.A_BOLD if is_selected else 0
            x = 0
            if is_me:
                content_width = max(1, width - 1)
                x = max(0, content_width - display_width(line))
            self._write(body_top + idx, x, line, attr)

        if info_bar_row is not None:
            if self.editing_msg_id is not None:
                guide = (
                    f"EDIT #{self.editing_msg_id} | Enter: save | "
                    "Ctrl+E: older | Ctrl+R: newer | Ctrl+G: cancel | Ctrl+D: delete selected"
                )
            elif self.search_query:
                if self.search_match_msg_ids:
                    guide = (
                        f"SEARCH '{self.search_query}' "
                        f"{self.search_match_idx + 1}/{len(self.search_match_msg_ids)} | "
                        "Ctrl+N: next | Ctrl+P: prev | Esc: clear"
                    )
                else:
                    guide = (
                        f"SEARCH '{self.search_query}' 0/0 | "
                        "/s <query> | Esc: clear"
                    )
            else:
                if self.chat_scroll_offset > 0:
                    guide = (
                        "Enter: bottom | "
                        "Ctrl+E: Select Message | /s <query>"
                    )
                else:
                    guide = (
                        "Ctrl+N: newline | "
                        "Ctrl+E: Select Message | /s <query>"
                    )
            guide_attr = curses.A_REVERSE
            guide_fill = " " * max(0, width - 1)
            self._write(info_bar_row, 0, guide_fill, guide_attr)
            self._write(
                info_bar_row,
                1,
                clip_to_width(guide, max(1, width - 2)),
                guide_attr | curses.A_BOLD,
            )

        render_rows = max(1, len(input_rows))
        rendered_input, cursor_x = self._render_input_lines(width, render_rows)
        if input_rows:
            for idx, row in enumerate(input_rows):
                prefix, text = rendered_input[idx]
                self._write(row, 0, prefix + text)
            cursor_y = input_rows[-1]
        else:
            prefix, text = rendered_input[-1]
            cursor_y = max(body_top, row_cursor)
            self._write(cursor_y, 0, prefix + text)

        self._draw_delete_confirm_modal()

        try:
            if self.delete_confirm_msg_id is not None:
                self.stdscr.move(0, 0)
            else:
                self.stdscr.move(cursor_y, cursor_x)
        except curses.error:
            pass

    def draw(self) -> None:
        self.stdscr.erase()
        if self.mode == "dialogs":
            self.draw_dialogs()
        else:
            self.draw_chat()
        self.stdscr.refresh()
        self.needs_redraw = False

    async def run(self) -> None:
        await self.refresh_dialogs()
        self._set_status("Dialog list loaded. Up/Down move, Enter open, Esc quit")
        self.draw()

        while self.running:
            await self.handle_input()

            now = time.monotonic()
            if (
                self.mode == "chat"
                and now - self.last_dialog_refresh >= self.auto_refresh_interval
            ):
                self.dialog_refresh_requested = True
                self._request_peer_status_refresh(force=False)

            if self.dialog_refresh_requested and now - self.last_dialog_refresh >= 1.0:
                self._request_refresh(quiet=self.mode == "chat")

            if self.mode == "chat" and self.input_buffer:
                self._request_peer_status_refresh(force=False)

            if self.needs_redraw:
                self.draw()

            await asyncio.sleep(0.03)

        for task in list(self._all_tasks):
            task.cancel()
        if self._all_tasks:
            await asyncio.gather(*self._all_tasks, return_exceptions=True)


def setup_curses() -> Any:
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass
    stdscr = curses.initscr()
    curses.noecho()
    curses.cbreak()
    stdscr.keypad(True)
    stdscr.nodelay(True)
    try:
        curses.curs_set(1)
    except curses.error:
        pass
    try:
        curses.set_escdelay(25)
    except Exception:
        pass
    return stdscr


def cleanup_curses(stdscr: Any) -> None:
    if stdscr is None:
        return
    try:
        stdscr.keypad(False)
    except Exception:
        pass
    try:
        curses.echo()
    except Exception:
        pass
    try:
        curses.nocbreak()
    except Exception:
        pass
    try:
        curses.endwin()
    except Exception:
        pass


async def async_main() -> int:
    load_dotenv()
    config_path = Path(os.getenv("TTG_CONFIG_PATH", "ttg_config.json"))
    config = load_app_config(config_path)
    logger = setup_logging(config)
    if config_path.exists():
        logger.info("Loaded config from %s", str(config_path))
    else:
        logger.info("Config file not found (%s), using defaults", str(config_path))

    try:
        api_id = int(env_required("TG_API_ID"))
        api_hash = env_required("TG_API_HASH")
    except RuntimeError as exc:
        logger.error("%s", str(exc))
        print(str(exc), file=sys.stderr)
        return 2
    except ValueError:
        logger.error("TG_API_ID must be an integer")
        print("TG_API_ID must be an integer.", file=sys.stderr)
        return 2

    session_name = os.getenv("TG_SESSION_NAME", "tg_terminal")
    client = TelegramClient(session_name, api_id, api_hash)
    logger.info("Starting Telegram client session=%s", session_name)
    try:
        await client.start()
        me = await client.get_me()
    except Exception as exc:
        logger.exception("Telegram connection failed")
        print(f"Telegram connection failed: {exc}", file=sys.stderr)
        await client.disconnect()
        return 1

    me_label = entity_label(me)
    logger.info("Connected as %s", me_label)
    print(f"Connected as {me_label}")
    await asyncio.sleep(0.2)

    stdscr = None
    try:
        stdscr = setup_curses()
        app = TerminalTelegramTUI(client, stdscr, config=config, logger=logger)
        client.add_event_handler(app.on_new_message, events.NewMessage(incoming=True))
        await app.run()
    finally:
        cleanup_curses(stdscr)
        await client.disconnect()
        logger.info("Client disconnected")
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
