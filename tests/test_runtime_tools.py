import json
import tempfile
import unittest
from pathlib import Path

from tg_client import AppConfig, cleanup_log_files, load_app_config, redact_log_text


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


if __name__ == "__main__":
    unittest.main()
