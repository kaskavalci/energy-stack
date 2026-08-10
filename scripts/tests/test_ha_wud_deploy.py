"""Regression tests for the WUD-only Home Assistant deployment path."""

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
WUD_ENV = (
    "WUD_TELEGRAM_BOT_TOKEN=bot-secret\n"
    "WUD_TELEGRAM_CHAT_ID=chat-secret\n"
    "TZ=Europe/Amsterdam\n"
)


class HaWudDeployTests(unittest.TestCase):
    def run_deploy(
        self,
        *arguments: str,
        validator_exit: int = 0,
        upload_failure: str | None = None,
        remote_shell: str = "/bin/sh",
    ) -> tuple[subprocess.CompletedProcess[str], Path, list[str], Path, Path]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "repo"
        log = Path(temporary.name) / "log"
        bin_dir = Path(temporary.name) / "bin"
        remote_root = Path(temporary.name) / "remote HA node"
        ha_config_dir = remote_root / "config data"
        compose_parent = remote_root / "selected Compose parent"
        compose_path = compose_parent / "custom compose.yml"
        env_path = compose_parent / "wud.env"
        for path in (
            root / "scripts" / "lib",
            root / "config",
            root / ".venv" / "bin",
            root / "home-assistant",
            log,
            bin_dir,
            ha_config_dir / "dashboards",
            compose_parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

        deploy_source = (REPO_ROOT / "scripts" / "deploy-home-assistant.sh").read_text(
            encoding="utf-8"
        )
        deploy_source = deploy_source.replace(
            '"/tmp/ha_configuration_live.yaml"', '"${TEST_TMP}/ha_configuration_live.yaml"'
        ).replace(
            '"/tmp/ha_configuration_merged.yaml"', '"${TEST_TMP}/ha_configuration_merged.yaml"'
        )
        deploy_path = root / "scripts" / "deploy-home-assistant.sh"
        deploy_path.write_text(deploy_source, encoding="utf-8")
        deploy_path.chmod(0o755)
        shutil.copy2(REPO_ROOT / "scripts" / "lib" / "source-site-env.sh", root / "scripts" / "lib")
        shutil.copy2(
            REPO_ROOT / "home-assistant" / "docker-compose.yml",
            root / "home-assistant" / "docker-compose.yml",
        )
        (root / "scripts" / "lib" / "validate_wud_env.py").write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            "from pathlib import Path\n"
            "Path(os.environ['TEST_LOG'], 'events').open('a', encoding='utf-8').write('validate-wud\\n')\n"
            "raise SystemExit(int(os.environ.get('TEST_WUD_VALIDATOR_EXIT', '0')))\n",
            encoding="utf-8",
        )
        (root / "config" / "site.env").write_text(
            "HA_SSH_USER=deploy\nHA_SSH_HOST=ha.test\nHA_SSH_PORT=2222\n"
            f"HA_CONFIG_DIR={shlex.quote(str(ha_config_dir))}\n"
            "HA_CONTAINER_NAME=homeassistant\n"
            f"HA_COMPOSE_DIR={shlex.quote(str(compose_parent))}\n",
            encoding="utf-8",
        )
        (root / "config" / "wud.env").write_text(WUD_ENV, encoding="utf-8")
        (ha_config_dir / "secrets.yaml").write_text(
            "emhass_publish_url: http://energy.test:5000/action/publish-data\n",
            encoding="utf-8",
        )
        (ha_config_dir / "configuration.yaml").write_text(
            "homeassistant:\n  name: Test Home\n",
            encoding="utf-8",
        )
        compose_path.write_text("services:\n  previous-service:\n    image: old:test\n", encoding="utf-8")
        (root / "scripts" / "render-ha-packages.sh").write_text(
            "#!/bin/sh\n"
            "mkdir -p \"$PWD/.rendered/home-assistant/dashboards\"\n"
            "touch \"$PWD/.rendered/home-assistant/ha_emhass.yaml\"\n"
            "touch \"$PWD/.rendered/home-assistant/ha_emhass_sensors.yaml\"\n"
            "touch \"$PWD/.rendered/home-assistant/ha_emhass_dashboard.yaml\"\n"
            "touch \"$PWD/.rendered/home-assistant/ha_emhass_monitoring.yaml\"\n"
            "touch \"$PWD/.rendered/home-assistant/ha_emhass_helpers.yaml\"\n"
            "touch \"$PWD/.rendered/home-assistant/ha_sigen_ems.yaml\"\n"
            "touch \"$PWD/.rendered/home-assistant/ha_sigen_diagnostics.yaml\"\n"
            "touch \"$PWD/.rendered/home-assistant/dashboards/emhass_trading.yaml\"\n"
            "touch \"$PWD/.rendered/home-assistant/dashboards/sigen_diagnostics.yaml\"\n",
            encoding="utf-8",
        )
        (root / "scripts" / "install-ha-apexcharts.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (root / ".venv" / "bin" / "python").write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-c\" ]; then exit 0; fi\n"
            "case \"$1\" in *validate_wud_env.py) exec python3 \"$@\" ;; esac\n"
            "while [ \"$#\" -gt 0 ]; do\n"
            "  if [ \"$1\" = \"--out\" ]; then touch \"$2\"; exit 0; fi\n"
            "  shift\n"
            "done\n",
            encoding="utf-8",
        )
        (bin_dir / "ssh").write_text(
            "#!/bin/sh\n"
            "number=$(($(cat \"$TEST_LOG/ssh-count\" 2>/dev/null || echo 0) + 1))\n"
            "printf '%s' \"$number\" > \"$TEST_LOG/ssh-count\"\n"
            "printf 'ssh %s\\n' \"$*\" >> \"$TEST_LOG/events\"\n"
            "printf '%s\\n' \"$@\" > \"$TEST_LOG/ssh-${number}.args\"\n"
            "cat > \"$TEST_LOG/ssh-${number}.stdin\"\n"
            "if [ \"${1:-}\" = \"-p\" ]; then shift 2; fi\n"
            "host=$1\n"
            "shift\n"
            "if [ \"$#\" -gt 0 ]; then\n"
            "  case \"$1\" in\n"
            "    *'.wud.env.XXXXXX'*) TEST_REMOTE_WUD_UPLOAD=1 ;;\n"
            "    *) TEST_REMOTE_WUD_UPLOAD=0 ;;\n"
            "  esac\n"
            "  export TEST_REMOTE_WUD_UPLOAD\n"
            "  remote_shell=\"${TEST_REMOTE_SHELL:-/bin/sh}\"\n"
            "  exec \"$remote_shell\" -c \"$1\" < \"$TEST_LOG/ssh-${number}.stdin\"\n"
            "fi\n",
            encoding="utf-8",
        )
        (bin_dir / "cat").write_text(
            "#!/bin/sh\n"
            "if [ \"${TEST_REMOTE_WUD_UPLOAD:-0}\" = 1 ] && "
            "[ \"${TEST_WUD_UPLOAD_FAILURE:-}\" = cat ]; then exit 72; fi\n"
            "exec /bin/cat \"$@\"\n",
            encoding="utf-8",
        )
        (bin_dir / "mktemp").write_text(
            "#!/bin/sh\n"
            "if [ \"${TEST_REMOTE_WUD_UPLOAD:-0}\" = 1 ] && "
            "[ \"${TEST_WUD_UPLOAD_FAILURE:-}\" = mktemp ]; then exit 71; fi\n"
            "exec /usr/bin/mktemp \"$@\"\n",
            encoding="utf-8",
        )
        (bin_dir / "docker").write_text(
            "#!/bin/sh\n"
            "if [ \"${1:-}\" = \"compose\" ]; then log_name=docker-calls; else log_name=docker-direct-calls; fi\n"
            "{\n"
            "  printf 'BEGIN\\n'\n"
            "  printf '%s\\n' \"$@\"\n"
            "  printf 'END\\n'\n"
            "} >> \"$TEST_LOG/$log_name\"\n"
            "if [ \"${TEST_REJECT_DOCKER_RESTART:-0}\" = \"1\" ]; then\n"
            "  for argument in \"$@\"; do\n"
            "    if [ \"$argument\" = \"restart\" ]; then exit 97; fi\n"
            "  done\n"
            "fi\n"
            "if [ \"${1:-}\" = \"compose\" ]; then\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (bin_dir / "chmod").write_text(
            "#!/bin/sh\n"
            "{\n"
            "  printf 'BEGIN\\n'\n"
            "  printf '%s\\n' \"$@\"\n"
            "  printf 'END\\n'\n"
            "} >> \"$TEST_LOG/chmod-calls\"\n"
            "if [ \"${TEST_REMOTE_WUD_UPLOAD:-0}\" = 1 ] && "
            "[ \"${TEST_WUD_UPLOAD_FAILURE:-}\" = chmod ]; then exit 73; fi\n"
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
            "if [ \"${TEST_REMOTE_WUD_UPLOAD:-0}\" = 1 ] && "
            "[ \"${TEST_WUD_UPLOAD_FAILURE:-}\" = mv ]; then exit 74; fi\n"
            "exec /bin/mv \"$@\"\n",
            encoding="utf-8",
        )
        for command in (
            root / "scripts" / "render-ha-packages.sh",
            root / "scripts" / "install-ha-apexcharts.sh",
            root / ".venv" / "bin" / "python",
            root / "scripts" / "lib" / "validate_wud_env.py",
            *bin_dir.iterdir(),
        ):
            command.chmod(0o755)

        env = dict(
            os.environ,
            PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            TEST_LOG=str(log),
            TEST_TMP=temporary.name,
            TEST_WUD_VALIDATOR_EXIT=str(validator_exit),
            TEST_WUD_UPLOAD_FAILURE=upload_failure or "",
            TEST_REJECT_DOCKER_RESTART="1" if "--wud-only" in arguments else "0",
            TEST_REMOTE_SHELL=remote_shell,
            HA_COMPOSE_FILE=str(compose_path),
        )
        result = subprocess.run(
            [root / "scripts" / "deploy-home-assistant.sh", *arguments],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )
        events_path = log / "events"
        events = events_path.read_text(encoding="utf-8").splitlines() if events_path.exists() else []
        return result, log, events, compose_path, env_path

    def ssh_calls(self, log: Path) -> list[tuple[str, str]]:
        calls = []
        for args_path in sorted(
            log.glob("ssh-*.args"),
            key=lambda path: int(path.stem.removeprefix("ssh-")),
        ):
            number = args_path.stem.removeprefix("ssh-")
            calls.append(
                (
                    args_path.read_text(encoding="utf-8"),
                    (log / f"ssh-{number}.stdin").read_text(encoding="utf-8"),
                )
            )
        return calls

    def recorded_calls(self, path: Path) -> list[list[str]]:
        if not path.exists():
            return []
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

    def compose_calls(self, log: Path) -> list[list[str]]:
        return self.recorded_calls(log / "docker-calls")

    def direct_docker_calls(self, log: Path) -> list[list[str]]:
        return self.recorded_calls(log / "docker-direct-calls")

    def test_wud_only_validates_uploads_and_runs_selected_compose_without_ha_restart(self) -> None:
        result, log, events, compose_path, env_path = self.run_deploy("--wud-only")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validate-wud", events)
        self.assertLess(
            events.index("validate-wud"),
            next(i for i, event in enumerate(events) if event.startswith("ssh ")),
        )
        calls = self.ssh_calls(log)
        secret_calls = [(args, stdin) for args, stdin in calls if stdin == WUD_ENV]
        self.assertEqual(len(secret_calls), 1, "WUD secret must use one stdin-only SSH upload")
        args, stdin = secret_calls[0]
        self.assertEqual(stdin, WUD_ENV)
        self.assertNotIn("bot-secret", args)
        self.assertNotIn("chat-secret", args)
        executed_remote_command = args.splitlines()[-1]
        self.assertIn("umask 077", executed_remote_command)
        self.assertEqual(env_path, compose_path.parent / "wud.env")
        self.assertEqual(env_path.read_text(encoding="utf-8"), WUD_ENV)
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
        backups = list((compose_path.parent.parent / "config data" / "backups").glob("energy-stack-*"))
        self.assertEqual(len(backups), 1, "WUD-only deploy must create one HA-style backup")
        self.assertEqual(
            (backups[0] / compose_path.name).read_text(encoding="utf-8"),
            "services:\n  previous-service:\n    image: old:test\n",
        )

        moves_to_target = [
            call
            for call in self.recorded_calls(log / "mv-calls")
            if len(call) == 2 and Path(call[1]) == env_path
        ]
        self.assertEqual(len(moves_to_target), 1, "secret upload must make one atomic move")
        source, destination = map(Path, moves_to_target[0])
        self.assertNotEqual(source, destination)
        self.assertEqual(source.parent, env_path.parent)
        self.assertEqual(destination, env_path)
        self.assertIn(
            ["600", str(source)],
            self.recorded_calls(log / "chmod-calls"),
            "chmod must apply to the exact temporary path moved to wud.env",
        )
        self.assertFalse(source.exists(), "temporary secret path must be consumed by the move")

        compose_calls = self.compose_calls(log)
        operations = {
            "config --quiet": ["config", "--quiet"],
            "pull wud": ["pull", "wud"],
            "up -d wud": ["up", "-d", "wud"],
        }
        self.assertEqual(
            len(compose_calls),
            len(operations),
            f"WUD-only deploy must run only config, pull, and up: {compose_calls}",
        )
        self.assertIn(" ", str(compose_path))
        self.assertIn(" ", str(compose_path.parent))
        for label, suffix in operations.items():
            matches = [call for call in compose_calls if call[-len(suffix) :] == suffix]
            self.assertEqual(len(matches), 1, f"WUD-only deploy must run Compose {label}")
            command = matches[0]
            self.assertEqual(command[0], "compose")
            self.assertEqual(command.count("-f"), 1)
            self.assertEqual(command[command.index("-f") + 1], str(compose_path))
            self.assertEqual(command.count("--env-file"), 1)
            self.assertEqual(command[command.index("--env-file") + 1], str(env_path))
        all_remote = "\n".join(args for args, _ in calls)
        self.assertNotIn("hass --script check_config", all_remote)
        direct_docker_calls = self.direct_docker_calls(log)
        self.assertFalse(
            any("restart" in command for command in direct_docker_calls),
            "WUD-only deploy must not run direct Docker restart commands: "
            f"{direct_docker_calls}",
        )
        self.assertFalse(
            any("restart" in command for command in compose_calls),
            f"WUD-only deploy must not restart a Compose service: {compose_calls}",
        )

    def test_failing_validator_stops_before_ssh(self) -> None:
        result, _, events, _, _ = self.run_deploy("--wud-only", validator_exit=23)
        self.assertIn("validate-wud", events)
        self.assertFalse(any(event.startswith("ssh ") for event in events), events)
        self.assertNotEqual(result.returncode, 0)

    def test_wud_upload_failures_stop_before_compose_and_remove_temporary_files(self) -> None:
        for failed_command in ("mktemp", "cat", "chmod", "mv"):
            with self.subTest(failed_command=failed_command):
                result, log, _, _, env_path = self.run_deploy(
                    "--wud-only", upload_failure=failed_command
                )
                self.assertNotEqual(result.returncode, 0)
                calls = self.ssh_calls(log)
                secret_upload_indexes = [
                    index for index, (_, stdin) in enumerate(calls) if stdin == WUD_ENV
                ]
                self.assertEqual(secret_upload_indexes, [len(calls) - 1])
                self.assertEqual(
                    self.compose_calls(log),
                    [],
                    f"deployment continued after {failed_command} failed",
                )
                self.assertFalse(env_path.exists())
                self.assertFalse(list(env_path.parent.glob(".wud.env.*")))
                self.assertNotIn("WUD-only deploy complete", result.stdout)

    def test_wud_upload_works_when_ssh_login_shell_is_fish(self) -> None:
        fish = shutil.which("fish")
        if fish is None:
            self.skipTest("fish is required to reproduce the HA host login shell")

        result, log, _, _, env_path = self.run_deploy(
            "--wud-only", remote_shell=fish
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(env_path.read_text(encoding="utf-8"), WUD_ENV)
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
        upload_commands = [
            arguments
            for arguments, stdin in self.ssh_calls(log)
            if stdin == WUD_ENV
        ]
        self.assertEqual(len(upload_commands), 1)
        self.assertIn("bash -c", upload_commands[0])

    def test_unknown_option_exits_with_status_two_before_remote_calls(self) -> None:
        result, _, events, _, _ = self.run_deploy("--not-an-option")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(events, [])

    def test_normal_deploy_uploads_wud_resources_then_keeps_config_check_and_restart(self) -> None:
        result, log, _, compose_path, env_path = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.ssh_calls(log)
        remote = "\n".join(args for args, _ in calls)
        self.assertIn("hass --script check_config", remote)
        self.assertIn("restart", remote)
        self.assertIn("homeassistant", remote)
        self.assertEqual(
            compose_path.read_text(encoding="utf-8"),
            (REPO_ROOT / "home-assistant" / "docker-compose.yml").read_text(encoding="utf-8"),
        )
        self.assertEqual(env_path.read_text(encoding="utf-8"), WUD_ENV)
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
        secret_upload_indexes = [
            index for index, (_, stdin) in enumerate(calls) if stdin == WUD_ENV
        ]
        self.assertEqual(len(secret_upload_indexes), 1)
        restart_index = next(
            index for index, (arguments, _) in enumerate(calls) if " restart " in arguments
        )
        self.assertLess(secret_upload_indexes[0], restart_index)
        self.assertEqual(
            self.compose_calls(log),
            [["compose", "-f", str(compose_path), "--env-file", str(env_path), "restart", "homeassistant"]],
        )


if __name__ == "__main__":
    unittest.main()
