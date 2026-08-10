"""Regression tests for secure WUD deployment to the energy host."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


class WudEnergyDeployTests(unittest.TestCase):
    def run_deploy(
        self, *, validator_exit: int = 0, mv_exit: int = 0, use_default_remote_dir: bool = False
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path, list[str]]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repo"
        log = Path(temporary.name) / "log"
        bin_dir = Path(temporary.name) / "bin"
        home_dir = Path(temporary.name) / "home"
        remote_dir = (
            home_dir / "workspace" / "energy-stack"
            if use_default_remote_dir
            else Path(temporary.name) / "remote dir; safe"
        )
        for path in (root / "scripts" / "lib", root / "config", log, bin_dir, remote_dir / "config"):
            path.mkdir(parents=True, exist_ok=True)

        shutil.copy2(REPO_ROOT / "scripts" / "deploy-energy-stack.sh", root / "scripts")
        for name in ("source-site-env.sh", "validate_site_location.py"):
            shutil.copy2(REPO_ROOT / "scripts" / "lib" / name, root / "scripts" / "lib")
        (root / "scripts" / "lib" / "validate_wud_env.py").write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['TEST_LOG'], 'events').open('a', encoding='utf-8').write('validate-wud\\n')\n"
            "raise SystemExit(int(os.environ.get('TEST_WUD_VALIDATOR_EXIT', '0')))\n",
            encoding="utf-8",
        )
        site_env = (
            "LAT=52\nLON=5\nALT=0\nTIME_ZONE=Europe/Amsterdam\nTZ=Europe/Amsterdam\n"
            "ENERGY_REMOTE_HOST=energy.test\n"
        )
        if not use_default_remote_dir:
            site_env += f"ENERGY_REMOTE_DIR={shlex.quote(str(remote_dir))}\n"
        (root / "config" / "site.env").write_text(
            site_env,
            encoding="utf-8",
        )
        (root / "config" / "wud.env").write_text(
            "WUD_TELEGRAM_BOT_TOKEN=bot-secret\n"
            "WUD_TELEGRAM_CHAT_ID=chat-secret\nTZ=Europe/Amsterdam\n",
            encoding="utf-8",
        )
        (bin_dir / "rsync").write_text(
            "#!/bin/sh\n"
            "printf 'rsync %s\\n' \"$*\" >> \"$TEST_LOG/events\"\n"
            "{\n"
            "  printf 'BEGIN\\n'\n"
            "  printf '%s\\n' \"$@\"\n"
            "  printf 'END\\n'\n"
            "} >> \"$TEST_LOG/rsync-calls\"\n",
            encoding="utf-8",
        )
        (bin_dir / "ssh").write_text(
            "#!/bin/sh\n"
            "number=$(($(cat \"$TEST_LOG/ssh-count\" 2>/dev/null || echo 0) + 1))\n"
            "printf '%s' \"$number\" > \"$TEST_LOG/ssh-count\"\n"
            "printf 'ssh %s\\n' \"$*\" >> \"$TEST_LOG/events\"\n"
            "printf '%s\\n' \"$@\" > \"$TEST_LOG/ssh-${number}.args\"\n"
            "cat > \"$TEST_LOG/ssh-${number}.stdin\"\n"
            "host=$1\nshift\n"
            "if [ \"$#\" -gt 0 ]; then /bin/sh -c \"$1\" < \"$TEST_LOG/ssh-${number}.stdin\"; fi\n",
            encoding="utf-8",
        )
        (bin_dir / "docker").write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$TEST_LOG/docker-calls\"\n",
            encoding="utf-8",
        )
        (bin_dir / "chmod").write_text(
            "#!/bin/sh\n"
            "{\n"
            "  printf 'BEGIN\\n'\n"
            "  printf '%s\\n' \"$@\"\n"
            "  printf 'END\\n'\n"
            "} >> \"$TEST_LOG/chmod-calls\"\n"
            "exec /bin/chmod \"$@\"\n",
            encoding="utf-8",
        )
        (bin_dir / "mv").write_text(
            "#!/bin/sh\n"
            "{\n"
            "  printf 'BEGIN\\n'\n"
            "  printf '%s\\n' \"$@\"\n"
            "  printf 'END\\n'\n"
            "} >> \"$TEST_LOG/mv-calls\"\n"
            "if [ \"${TEST_MV_EXIT:-0}\" -ne 0 ]; then exit \"$TEST_MV_EXIT\"; fi\n"
            "exec /bin/mv \"$@\"\n",
            encoding="utf-8",
        )
        (bin_dir / "rm").write_text(
            "#!/bin/sh\n"
            "{\n"
            "  printf 'BEGIN\\n'\n"
            "  printf '%s\\n' \"$@\"\n"
            "  printf 'END\\n'\n"
            "} >> \"$TEST_LOG/rm-calls\"\n"
            "exec /bin/rm \"$@\"\n",
            encoding="utf-8",
        )
        for command in (*bin_dir.iterdir(), root / "scripts" / "lib" / "validate_wud_env.py"):
            command.chmod(0o755)

        env = dict(
            os.environ,
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            TEST_LOG=str(log),
            TEST_WUD_VALIDATOR_EXIT=str(validator_exit),
            TEST_MV_EXIT=str(mv_exit),
            HOME=str(home_dir),
        )
        result = subprocess.run(
            [root / "scripts" / "deploy-energy-stack.sh"],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        if validator_exit == 0 and mv_exit == 0:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result, remote_dir, log, (log / "events").read_text(encoding="utf-8").splitlines()

    def recorded_calls(self, path: Path) -> list[list[str]]:
        calls: list[list[str]] = []
        current: list[str] | None = None
        for line in path.read_text(encoding="utf-8").splitlines():
            if line == "BEGIN":
                self.assertIsNone(current, f"nested call marker in {path}")
                current = []
            elif line == "END":
                self.assertIsNotNone(current, f"unmatched call marker in {path}")
                calls.append(current)
                current = None
            else:
                self.assertIsNotNone(current, f"argument outside call markers in {path}")
                current.append(line)
        self.assertIsNone(current, f"unterminated call marker in {path}")
        return calls

    def ssh_calls(self, log: Path) -> list[tuple[str, str]]:
        calls = []
        for args_path in sorted(log.glob("ssh-*.args")):
            number = args_path.stem.removeprefix("ssh-")
            calls.append(
                (
                    args_path.read_text(encoding="utf-8"),
                    (log / f"ssh-{number}.stdin").read_text(encoding="utf-8"),
                )
            )
        return calls

    def test_validates_and_excludes_wud_secret_before_rsync_or_ssh(self) -> None:
        _, _, _, events = self.run_deploy()
        self.assertIn("validate-wud", events)
        validation = events.index("validate-wud")
        self.assertLess(validation, next(index for index, event in enumerate(events) if event.startswith("rsync ")))
        self.assertLess(validation, next(index for index, event in enumerate(events) if event.startswith("ssh ")))
        self.assertIn("--exclude config/wud.env", next(event for event in events if event.startswith("rsync ")))

    def test_failing_validator_stops_before_rsync_or_ssh(self) -> None:
        result, _, _, events = self.run_deploy(validator_exit=23)
        self.assertIn("validate-wud", events)
        self.assertFalse(any(event.startswith("rsync ") for event in events), events)
        self.assertFalse(any(event.startswith("ssh ") for event in events), events)
        self.assertNotEqual(result.returncode, 0)

    def test_rsync_excludes_unrelated_root_files_and_sdd_scratch(self) -> None:
        _, _, log, _ = self.run_deploy()
        [arguments] = self.recorded_calls(log / "rsync-calls")
        exclusions = [
            arguments[index + 1]
            for index, argument in enumerate(arguments[:-1])
            if argument == "--exclude"
        ]
        for expected in ("/skills-lock.json", "/tmp_emhass_now.py", "/.superpowers/"):
            with self.subTest(expected=expected):
                self.assertIn(expected, exclusions)
        self.assertNotIn("/docker-compose.yml", exclusions)
        self.assertNotIn("docker-compose.yml", exclusions)

    def test_uploads_wud_secret_only_on_stdin_with_private_atomic_placement(self) -> None:
        _, remote_dir, log, _ = self.run_deploy()
        secret_calls = [(args, stdin) for args, stdin in self.ssh_calls(log) if stdin]
        self.assertEqual(len(secret_calls), 1, "WUD secret must use one stdin-only SSH upload")
        args, stdin = secret_calls[0]
        self.assertEqual(
            stdin,
            "WUD_TELEGRAM_BOT_TOKEN=bot-secret\nWUD_TELEGRAM_CHAT_ID=chat-secret\nTZ=Europe/Amsterdam\n",
        )
        self.assertNotIn("bot-secret", args)
        self.assertNotIn("chat-secret", args)
        executed_remote_command = args.splitlines()[-1]
        self.assertIn("umask 077", executed_remote_command)
        target = remote_dir / "config" / "wud.env"
        self.assertEqual(target.read_text(encoding="utf-8"), stdin)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        moves_to_target = [
            call
            for call in self.recorded_calls(log / "mv-calls")
            if len(call) == 2 and Path(call[1]) == target
        ]
        self.assertEqual(len(moves_to_target), 1, "secret upload must make one atomic move")
        source, destination = map(Path, moves_to_target[0])
        self.assertNotEqual(source, destination)
        self.assertEqual(source.parent, target.parent)
        self.assertEqual(destination, target)
        self.assertIn(
            ["600", str(source)],
            self.recorded_calls(log / "chmod-calls"),
            "chmod must apply to the exact temporary path moved to config/wud.env",
        )
        self.assertFalse(source.exists(), "temporary secret path must be consumed by the move")

    def test_failed_wud_upload_removes_temporary_secret_file(self) -> None:
        result, remote_dir, log, _ = self.run_deploy(mv_exit=23)
        self.assertNotEqual(result.returncode, 0)
        target = remote_dir / "config" / "wud.env"
        self.assertFalse(target.exists())
        moves_to_target = [
            call
            for call in self.recorded_calls(log / "mv-calls")
            if len(call) == 2 and Path(call[1]) == target
        ]
        self.assertEqual(len(moves_to_target), 1)
        source = Path(moves_to_target[0][0])
        self.assertFalse(source.exists(), "failed secret upload must remove its temporary file")
        self.assertFalse(list(target.parent.glob(".wud.env.*")))

    def test_remote_cleanup_uses_the_safe_remote_directory(self) -> None:
        _, remote_dir, log, _ = self.run_deploy()
        cleanup_args, cleanup_stdin = self.ssh_calls(log)[1]
        self.assertEqual(cleanup_stdin, "")
        self.assertIn("remote_dir=", cleanup_args.splitlines()[-1])
        self.assertIn(
            [
                "-f",
                str(remote_dir / "crontab"),
                str(remote_dir / "Dockerfile"),
                str(remote_dir / "fetch_prices_emhass.py"),
                str(remote_dir / "README.md"),
            ],
            self.recorded_calls(log / "rm-calls"),
        )

    def test_documented_remote_directory_fallback_expands_on_the_remote_host(self) -> None:
        _, remote_dir, _, _ = self.run_deploy(use_default_remote_dir=True)
        self.assertTrue((remote_dir / "config" / "wud.env").is_file())

    def test_remote_compose_calls_use_the_exact_required_sequence(self) -> None:
        _, _, log, _ = self.run_deploy()
        commands = (log / "docker-calls").read_text(encoding="utf-8").splitlines()
        self.assertEqual(
            commands,
            [
                "compose --env-file config/wud.env config --quiet",
                "compose --env-file config/wud.env pull wud",
                "compose --env-file config/wud.env build emhass-cron energy-monitor",
                "compose --env-file config/wud.env up -d --force-recreate emhass",
                "compose --env-file config/wud.env up -d emhass-cron energy-monitor wud",
            ],
        )


if __name__ == "__main__":
    unittest.main()
