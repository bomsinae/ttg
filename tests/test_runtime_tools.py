import logging
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tg_client import (
    AppConfig,
    cleanup_log_files,
    load_app_config,
    redact_log_text,
    setup_logging,
    TerminalTelegramTUI,
)


class DummyStdScr:
    def getmaxyx(self):
        return (24, 80)

    def addstr(self, *args, **kwargs):
        return None

    def move(self, *args, **kwargs):
        return None


class RuntimeToolsTests(unittest.TestCase):
    def test_redact_log_text_masks_secret_and_phone(self) -> None:
        source = "hash=abc123 phone=+1 (415) 555-0199"
        cleaned = redact_log_text(
            source,
            redact_secrets=True,
            redact_phone_numbers=True,
            sensitive_values=["abc123"],
        )
        self.assertNotIn("abc123", cleaned)
        self.assertNotIn("555-0199", cleaned)
        self.assertIn("[redacted]", cleaned)
        self.assertIn("[redacted-phone]", cleaned)

    def test_cleanup_log_files_removes_base_and_numeric_backups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "ttg.log").write_text("a", encoding="utf-8")
            (root / "ttg.log.1").write_text("b", encoding="utf-8")
            (root / "ttg.log.2").write_text("c", encoding="utf-8")
            (root / "ttg.log.bad").write_text("d", encoding="utf-8")

            config = AppConfig(log_file=str(root / "ttg.log"))
            removed, failures = cleanup_log_files(config)

            removed_names = sorted(path.name for path in removed)
            self.assertEqual(removed_names, ["ttg.log", "ttg.log.1", "ttg.log.2"])
            self.assertEqual(failures, [])
            self.assertTrue((root / "ttg.log.bad").exists())

    def test_load_app_config_reads_logging_extensions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "ttg_config.json"
            payload = {
                "logging": {
                    "file": "custom.log",
                    "level": "debug",
                    "max_bytes": 2048,
                    "backup_count": 7,
                    "redact_secrets": False,
                    "redact_phone_numbers": False,
                }
            }
            config_path.write_text(json.dumps(payload), encoding="utf-8")
            config = load_app_config(config_path)

            self.assertEqual(config.log_file, "custom.log")
            self.assertEqual(config.log_level, "DEBUG")
            self.assertEqual(config.log_max_bytes, 2048)
            self.assertEqual(config.log_backup_count, 7)
            self.assertFalse(config.log_redact_secrets)
            self.assertFalse(config.log_redact_phone_numbers)

    def test_setup_logging_suppresses_telethon_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = AppConfig(log_file=str(Path(tmp) / "ttg.log"))
            logger = setup_logging(config)
            telethon_logger = logging.getLogger("telethon")

            self.assertIs(logger.handlers[0], telethon_logger.handlers[0])
            self.assertFalse(telethon_logger.propagate)
            self.assertEqual(telethon_logger.level, logging.ERROR)

    def test_preview_mode_from_env_accepts_known_values(self) -> None:
        self.assertEqual(TerminalTelegramTUI._preview_mode_from_env(None), "auto")
        self.assertEqual(TerminalTelegramTUI._preview_mode_from_env("ansi"), "ansi")
        self.assertEqual(TerminalTelegramTUI._preview_mode_from_env("sixel"), "sixel")
        self.assertEqual(TerminalTelegramTUI._preview_mode_from_env("weird"), "auto")

    def test_should_use_sixel_preview_honors_force_mode(self) -> None:
        app = TerminalTelegramTUI(client=object(), stdscr=DummyStdScr())
        with patch("tg_client.shutil.which", return_value="/usr/bin/img2sixel"):
            with patch.dict(
                "os.environ",
                {"TTG_IMAGE_PREVIEW_MODE": "sixel", "TERM": "tmux-256color"},
                clear=False,
            ):
                self.assertTrue(app._should_use_sixel_preview())

    def test_should_use_sixel_preview_enables_auto_inside_tmux_when_client_reports_sixel(self) -> None:
        app = TerminalTelegramTUI(client=object(), stdscr=DummyStdScr())
        with patch("tg_client.shutil.which", return_value="/usr/bin/img2sixel"):
            with patch.object(app, "_tmux_client_supports_sixel", return_value=True):
                with patch.dict(
                    "os.environ",
                    {"TTG_IMAGE_PREVIEW_MODE": "auto", "TERM": "tmux-256color", "TMUX": "1"},
                    clear=False,
                ):
                    self.assertTrue(app._should_use_sixel_preview())

    def test_should_use_sixel_preview_disables_auto_inside_tmux_without_client_sixel(self) -> None:
        app = TerminalTelegramTUI(client=object(), stdscr=DummyStdScr())
        with patch("tg_client.shutil.which", return_value="/usr/bin/img2sixel"):
            with patch.object(app, "_tmux_client_supports_sixel", return_value=False):
                with patch.dict(
                    "os.environ",
                    {"TTG_IMAGE_PREVIEW_MODE": "auto", "TERM": "tmux-256color", "TMUX": "1"},
                    clear=False,
                ):
                    self.assertFalse(app._should_use_sixel_preview())

    def test_should_use_sixel_preview_enables_auto_outside_tmux_when_img2sixel_exists(self) -> None:
        app = TerminalTelegramTUI(client=object(), stdscr=DummyStdScr())
        with patch("tg_client.shutil.which", return_value="/usr/bin/img2sixel"):
            with patch.dict(
                "os.environ",
                {"TTG_IMAGE_PREVIEW_MODE": "auto", "TERM": "xterm-256color", "TMUX": ""},
                clear=False,
            ):
                self.assertTrue(app._should_use_sixel_preview())

    def test_tmux_client_supports_sixel_parses_feature_list(self) -> None:
        completed = type("Completed", (), {"stdout": "clipboard,sixel,title\n"})()
        with patch("tg_client.subprocess.run", return_value=completed):
            self.assertTrue(TerminalTelegramTUI._tmux_client_supports_sixel())

    def test_tmux_client_supports_sixel_returns_false_without_feature(self) -> None:
        completed = type("Completed", (), {"stdout": "clipboard,title\n"})()
        with patch("tg_client.subprocess.run", return_value=completed):
            self.assertFalse(TerminalTelegramTUI._tmux_client_supports_sixel())


if __name__ == "__main__":
    unittest.main()
