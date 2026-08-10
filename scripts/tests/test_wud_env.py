"""Regression tests for the isolated WUD environment file."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))

from validate_wud_env import load_wud_env, validate_wud_env  # noqa: E402


VALID_ENV = {
    "WUD_TELEGRAM_BOT_TOKEN": "123456:telegram-token",
    "WUD_TELEGRAM_CHAT_ID": "-1001234567890",
    "TZ": "Europe/Amsterdam",
}


class WudEnvironmentValidationTests(unittest.TestCase):
    def assert_errors_hide_valid_secrets(self, values: dict[str, str]) -> list[str]:
        errors = validate_wud_env(values)
        rendered = "\n".join(errors)
        self.assertNotIn(VALID_ENV["WUD_TELEGRAM_BOT_TOKEN"], rendered)
        self.assertNotIn(VALID_ENV["WUD_TELEGRAM_CHAT_ID"], rendered)
        for field in ("WUD_TELEGRAM_BOT_TOKEN", "WUD_TELEGRAM_CHAT_ID"):
            if value := values.get(field, ""):
                self.assertNotIn(value, rendered)
        return errors

    def test_valid_environment_has_no_errors(self) -> None:
        self.assertEqual(validate_wud_env(VALID_ENV), [])

    def test_missing_bot_token_is_rejected(self) -> None:
        values = dict(VALID_ENV)
        del values["WUD_TELEGRAM_BOT_TOKEN"]
        self.assertIn("WUD_TELEGRAM_BOT_TOKEN is required", self.assert_errors_hide_valid_secrets(values))

    def test_missing_chat_id_is_rejected(self) -> None:
        values = dict(VALID_ENV)
        del values["WUD_TELEGRAM_CHAT_ID"]
        self.assertIn("WUD_TELEGRAM_CHAT_ID is required", self.assert_errors_hide_valid_secrets(values))

    def test_invalid_timezone_is_rejected(self) -> None:
        values = dict(VALID_ENV, TZ="Europe/Not_A_Zone")
        self.assertIn(
            "TZ must name an installed IANA timezone", self.assert_errors_hide_valid_secrets(values)
        )

    def test_blank_secrets_are_rejected(self) -> None:
        values = dict(VALID_ENV, WUD_TELEGRAM_BOT_TOKEN="   ", WUD_TELEGRAM_CHAT_ID="")
        errors = self.assert_errors_hide_valid_secrets(values)
        self.assertIn("WUD_TELEGRAM_BOT_TOKEN is required", errors)
        self.assertIn("WUD_TELEGRAM_CHAT_ID is required", errors)

    def test_invalid_secret_values_are_rejected_without_disclosure(self) -> None:
        values = dict(
            VALID_ENV,
            WUD_TELEGRAM_BOT_TOKEN="invalid bot token",
            WUD_TELEGRAM_CHAT_ID="invalid chat id",
        )
        self.assertIn(
            "WUD_TELEGRAM_BOT_TOKEN must not contain whitespace",
            self.assert_errors_hide_valid_secrets(values),
        )
        self.assertIn(
            "WUD_TELEGRAM_CHAT_ID must not contain whitespace",
            self.assert_errors_hide_valid_secrets(values),
        )


class WudEnvironmentFileTests(unittest.TestCase):
    def write_env(self, text: str) -> Path:
        temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        with temporary:
            temporary.write(text)
        return Path(temporary.name)

    def test_loads_valid_key_value_file(self) -> None:
        path = self.write_env(
            "# WUD secrets\n\n"
            "WUD_TELEGRAM_BOT_TOKEN=123456:telegram-token\n"
            "WUD_TELEGRAM_CHAT_ID=-1001234567890\n"
            "TZ=Europe/Amsterdam\n"
        )
        self.assertEqual(load_wud_env(path), VALID_ENV)

    def test_duplicate_keys_are_rejected_without_disclosing_secret(self) -> None:
        secret = "first-secret-value"
        path = self.write_env(
            f"WUD_TELEGRAM_BOT_TOKEN={secret}\n"
            "WUD_TELEGRAM_BOT_TOKEN=second-secret-value\n"
        )
        with self.assertRaisesRegex(ValueError, "duplicate key: WUD_TELEGRAM_BOT_TOKEN") as raised:
            load_wud_env(path)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("second-secret-value", str(raised.exception))

    def test_malformed_keys_are_rejected_without_disclosing_secret(self) -> None:
        secret = "do-not-print-this"
        path = self.write_env(f"wud_telegram_bot_token={secret}\n")
        with self.assertRaisesRegex(ValueError, "invalid key") as raised:
            load_wud_env(path)
        self.assertNotIn(secret, str(raised.exception))

    def test_malformed_line_is_rejected_without_disclosing_secret(self) -> None:
        secret = "do-not-print-this"
        path = self.write_env(f"WUD_TELEGRAM_BOT_TOKEN {secret}\n")
        with self.assertRaisesRegex(ValueError, "expected KEY=VALUE") as raised:
            load_wud_env(path)
        self.assertNotIn(secret, str(raised.exception))


class WudEnvironmentCliTests(unittest.TestCase):
    def write_env(self, text: str) -> Path:
        temporary = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        self.addCleanup(Path(temporary.name).unlink, missing_ok=True)
        with temporary:
            temporary.write(text)
        return Path(temporary.name)

    def run_cli(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, REPO_ROOT / "scripts" / "lib" / "validate_wud_env.py", path],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_returns_zero_for_valid_environment(self) -> None:
        path = self.write_env(
            "WUD_TELEGRAM_BOT_TOKEN=synthetic-token\n"
            "WUD_TELEGRAM_CHAT_ID=synthetic-chat-id\n"
            "TZ=Europe/Amsterdam\n"
        )
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")

    def test_cli_returns_one_without_disclosing_invalid_secrets(self) -> None:
        token = "synthetic token value"
        chat_id = "synthetic chat id"
        path = self.write_env(
            f"WUD_TELEGRAM_BOT_TOKEN={token}\n"
            f"WUD_TELEGRAM_CHAT_ID={chat_id}\n"
            "TZ=Europe/Amsterdam\n"
        )
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertNotIn(chat_id, result.stdout + result.stderr)

    def test_cli_returns_two_without_disclosing_parse_error_secrets(self) -> None:
        token = "synthetic-token-value"
        chat_id = "synthetic-chat-id"
        path = self.write_env(f"WUD_TELEGRAM_BOT_TOKEN {token} {chat_id}\n")
        result = self.run_cli(path)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(token, result.stdout + result.stderr)
        self.assertNotIn(chat_id, result.stdout + result.stderr)

    def test_cli_returns_two_for_missing_environment_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "missing.env"
            result = self.run_cli(path)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
