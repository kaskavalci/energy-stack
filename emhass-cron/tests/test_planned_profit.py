import importlib.util
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "emhass-cron"))
MODULE_PATH = REPO_ROOT / "emhass-cron" / "planned_profit.py"
SPEC = importlib.util.spec_from_file_location("planned_profit", MODULE_PATH)
planned_profit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = planned_profit
assert SPEC.loader is not None
SPEC.loader.exec_module(planned_profit)

CEST = ZoneInfo("Europe/Amsterdam")


class TargetDateTests(unittest.TestCase):
    def test_target_is_next_local_calendar_day(self):
        now = datetime(2026, 8, 16, 16, 30, tzinfo=CEST)
        self.assertEqual(str(planned_profit.target_date(now)), "2026-08-17")

    def test_forecast_detects_tomorrow_slot(self):
        now = datetime(2026, 8, 16, 16, 30, tzinfo=CEST)
        forecast = [
            {"datetime": "2026-08-16T14:00:00+00:00", "electricity_price": 1},
            {"datetime": "2026-08-17T00:00:00+00:00", "electricity_price": 1},
        ]
        self.assertTrue(planned_profit.forecast_has_target_date(forecast, now=now))
        self.assertFalse(
            planned_profit.forecast_has_target_date(forecast[:1], now=now)
        )


class CoveredProfitTests(unittest.TestCase):
    def test_sums_only_target_date_finite_cost_profit(self):
        rows = [
            planned_profit.OptResRow(
                datetime(2026, 8, 16, 23, 30, tzinfo=CEST), 1.0, "Optimal"
            ),
            planned_profit.OptResRow(
                datetime(2026, 8, 17, 0, 0, tzinfo=CEST), 1.111, "Optimal"
            ),
            planned_profit.OptResRow(
                datetime(2026, 8, 17, 0, 30, tzinfo=CEST), -0.211, "Optimal"
            ),
            planned_profit.OptResRow(
                datetime(2026, 8, 17, 1, 0, tzinfo=CEST), float("nan"), "Optimal"
            ),
        ]
        got = planned_profit.covered_profit(
            rows, datetime(2026, 8, 17, tzinfo=CEST).date()
        )
        self.assertEqual(got["planned_eur"], 0.90)
        self.assertEqual(got["slot_count"], 2)
        self.assertEqual(got["horizon_slots"], 4)
        self.assertEqual(got["coverage_start"], "2026-08-17T00:00:00+02:00")
        self.assertEqual(got["coverage_end_exclusive"], "2026-08-17T01:00:00+02:00")
        self.assertTrue(got["partial"])

    def test_none_when_no_matching_rows(self):
        rows = [
            planned_profit.OptResRow(
                datetime(2026, 8, 16, 15, 0, tzinfo=CEST), 1.0, "Optimal"
            )
        ]
        self.assertIsNone(
            planned_profit.covered_profit(
                rows, datetime(2026, 8, 17, tzinfo=CEST).date()
            )
        )

    def test_naive_timestamp_treated_as_utc(self):
        naive = datetime(2026, 8, 16, 22, 0)
        rows = [planned_profit.OptResRow(naive, 2.0, "Optimal")]
        got = planned_profit.covered_profit(
            rows, datetime(2026, 8, 17, tzinfo=CEST).date()
        )
        self.assertEqual(got["slot_count"], 1)

    def test_dst_spring_forward_local_dates(self):
        rows = [
            planned_profit.OptResRow(
                datetime(2026, 3, 29, 1, 30, tzinfo=CEST), 0.1, "Optimal"
            ),
            planned_profit.OptResRow(
                datetime(2026, 3, 29, 3, 0, tzinfo=CEST), 0.2, "Optimal"
            ),
        ]
        got = planned_profit.covered_profit(
            rows, datetime(2026, 3, 29, tzinfo=CEST).date()
        )
        self.assertEqual(got["slot_count"], 2)
        self.assertEqual(got["planned_eur"], 0.3)


class ParseOptResTests(unittest.TestCase):
    def test_parse_payload_rejects_missing_cost_profit(self):
        with self.assertRaises(ValueError):
            planned_profit.parse_opt_res_payload(
                {"rows": [{"timestamp": "2026-08-17T00:00:00+02:00"}]}
            )

    def test_parse_payload_skips_non_finite_then_groups(self):
        payload = {
            "optim_status": "Optimal",
            "rows": [
                {
                    "timestamp": "2026-08-17T00:00:00+02:00",
                    "cost_profit": 1.0,
                    "optim_status": "Optimal",
                },
            ],
        }
        rows = planned_profit.parse_opt_res_payload(payload)
        self.assertEqual(len(rows), 1)


class ReadOptResViaDockerTests(unittest.TestCase):
    def test_read_opt_res_via_docker_uses_runner(self):
        payload = {
            "optim_status": "Optimal",
            "row_count": 1,
            "rows": [
                {
                    "timestamp": "2026-08-17T00:00:00+02:00",
                    "cost_profit": 1.0,
                    "optim_status": "Optimal",
                }
            ],
        }

        def runner(cmd, **kwargs):
            self.assertEqual(cmd[0:4], ["docker", "exec", "emhass", "python3"])
            self.assertEqual(cmd[-1], "/data/opt_res_latest.csv")
            result = MagicMock()
            result.returncode = 0
            result.stdout = json.dumps(payload)
            result.stderr = ""
            return result

        got = planned_profit.read_opt_res_via_docker(
            container="emhass", runner=runner
        )
        self.assertEqual(got, payload)

    def test_read_opt_res_via_docker_raises_on_failure(self):
        def runner(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 1
            result.stdout = ""
            result.stderr = "boom"
            return result

        with self.assertRaisesRegex(RuntimeError, "docker exec"):
            planned_profit.read_opt_res_via_docker(container="emhass", runner=runner)


class PersistLockTests(unittest.TestCase):
    @staticmethod
    def sample_record(*, plan_date: str = "2026-08-17", planned_eur: float = 3.45) -> dict:
        return {
            "plan_date": plan_date,
            "planned_eur": planned_eur,
            "slot_count": 12,
            "horizon_slots": 32,
            "coverage_start": "2026-08-17T00:00:00+02:00",
            "coverage_end_exclusive": "2026-08-17T06:00:00+02:00",
            "partial": True,
            "locked_at": "2026-08-16T14:00:52+02:00",
            "mpc_completed_at": "2026-08-16T14:00:52+02:00",
            "locked_on_date": "2026-08-16",
        }

    def test_write_once_does_not_overwrite(self):
        now = datetime(2026, 8, 16, 14, 0, tzinfo=CEST)
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            first = self.sample_record(planned_eur=3.45)
            second = self.sample_record(planned_eur=9.99)
            self.assertEqual(planned_profit.persist_lock(share, first, now=now), "written")
            self.assertEqual(planned_profit.persist_lock(share, second, now=now), "exists")
            record = planned_profit.day_record(share, date(2026, 8, 17))
            self.assertEqual(record["planned_eur"], 3.45)

    def test_prune_removes_only_keys_older_than_14_local_days(self):
        now = datetime(2026, 8, 16, 14, 0, tzinfo=CEST)
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            store = planned_profit.load_store(share)
            store["days"] = {
                "2026-07-01": self.sample_record(plan_date="2026-07-01"),
                "2026-08-16": self.sample_record(plan_date="2026-08-16"),
                "2026-08-18": self.sample_record(plan_date="2026-08-18"),
            }
            (share / planned_profit.JSON_NAME).write_text(
                json.dumps(store), encoding="utf-8"
            )
            planned_profit.persist_lock(
                share,
                self.sample_record(plan_date="2026-08-17"),
                now=now,
            )
            days = planned_profit.load_store(share)["days"]
            self.assertNotIn("2026-07-01", days)
            self.assertIn("2026-08-16", days)
            self.assertIn("2026-08-17", days)
            self.assertIn("2026-08-18", days)

    def test_malformed_store_does_not_overwrite(self):
        now = datetime(2026, 8, 16, 14, 0, tzinfo=CEST)
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            path = share / planned_profit.JSON_NAME
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaises(planned_profit.StoreCorruptError):
                planned_profit.persist_lock(
                    share,
                    self.sample_record(),
                    now=now,
                )
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")

    def test_helper_payload_stays_under_255(self):
        payload = planned_profit.helper_payload(self.sample_record())
        self.assertLessEqual(len(payload), planned_profit.MAX_HELPER_CHARS)
        self.assertNotIn(" ", payload)

    def test_concurrent_persist_writes_one_record(self):
        now = datetime(2026, 8, 16, 14, 0, tzinfo=CEST)
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            lock_path = share / planned_profit.LOCK_NAME
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(lock_path, "a+", encoding="utf-8")
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            results: list[str] = []

            def worker():
                results.append(
                    planned_profit.persist_lock(
                        share,
                        self.sample_record(),
                        now=now,
                    )
                )

            thread = threading.Thread(target=worker)
            thread.start()
            thread.join(timeout=2.0)
            self.assertTrue(thread.is_alive())

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            lock_file.close()
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0], "written")
            self.assertIsNotNone(planned_profit.day_record(share, date(2026, 8, 17)))


class PublishHaInputTextTests(unittest.TestCase):
    def setUp(self):
        self.secrets = Path(tempfile.mkdtemp()) / "secrets.yaml"
        self.secrets.write_text(
            "hass_url: http://ha.local:8123\nlong_lived_token: test-token\n",
            encoding="utf-8",
        )

    @staticmethod
    def response():
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b"[]"
        return response

    @patch.object(planned_profit, "urlopen")
    def test_helper_payload_posts_input_text_set_value(self, mock_urlopen):
        mock_urlopen.return_value = self.response()
        payload = planned_profit.helper_payload(
            PersistLockTests.sample_record()
        )

        planned_profit.publish_ha_input_text(
            self.secrets,
            planned_profit.HELPER_ENTITY_ID,
            payload,
        )

        request = mock_urlopen.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/api/services/input_text/set_value"))


class RunPlannedProfitSideEffectTests(unittest.TestCase):
    @staticmethod
    def snapshot(*, forecast):
        return MagicMock(
            state={"attributes": {"forecast": forecast}},
        )

    @staticmethod
    def docker_payload(*, plan_date: str, slots: int = 2) -> dict:
        base = datetime.fromisoformat(f"{plan_date}T00:00:00+02:00")
        return {
            "optim_status": "Optimal",
            "row_count": slots,
            "rows": [
                {
                    "timestamp": (base + timedelta(minutes=30 * index)).isoformat(),
                    "cost_profit": 0.5,
                    "optim_status": "Optimal",
                }
                for index in range(slots)
            ],
        }

    def test_no_lock_without_tomorrow_slots(self):
        completed_at = datetime(2026, 8, 16, 16, 30, tzinfo=CEST)
        forecast = [
            {"datetime": "2026-08-16T14:00:00+00:00", "electricity_price": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            with patch.object(
                planned_profit,
                "read_opt_res_via_docker",
            ) as read_docker:
                planned_profit.run_planned_profit_side_effect(
                    share_dir=share,
                    snapshot=self.snapshot(forecast=forecast),
                    completed_at=completed_at,
                    secrets_path=Path("/nonexistent/secrets.yaml"),
                )
            read_docker.assert_not_called()
            self.assertIsNone(planned_profit.day_record(share, date(2026, 8, 17)))
            status = json.loads(
                (share / "last_enever_run.json").read_text(encoding="utf-8")
            )
            self.assertTrue(status["planned_profit"]["ok"])
            self.assertFalse(status["planned_profit"]["locked"])

    def test_noop_clears_prior_planned_profit_failure(self):
        completed_at = datetime(2026, 8, 16, 16, 30, tzinfo=CEST)
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            planned_profit._write_planned_profit_status(
                share, ok=False, error="docker read failed"
            )
            planned_profit.run_planned_profit_side_effect(
                share_dir=share,
                snapshot=self.snapshot(forecast=[]),
                completed_at=completed_at,
                secrets_path=Path("/nonexistent/secrets.yaml"),
            )
            status = json.loads(
                (share / "last_enever_run.json").read_text(encoding="utf-8")
            )
            self.assertTrue(status["planned_profit"]["ok"])
            self.assertNotIn("error", status["planned_profit"])

    def test_corrupt_store_is_aux_failure_and_keeps_file(self):
        completed_at = datetime(2026, 8, 16, 16, 30, tzinfo=CEST)
        forecast = [
            {"datetime": "2026-08-17T00:00:00+00:00", "electricity_price": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            path = share / planned_profit.JSON_NAME
            path.write_text("{not json", encoding="utf-8")
            planned_profit.run_planned_profit_side_effect(
                share_dir=share,
                snapshot=self.snapshot(forecast=forecast),
                completed_at=completed_at,
                secrets_path=Path("/nonexistent/secrets.yaml"),
                docker_runner=lambda *args, **kwargs: MagicMock(
                    returncode=0,
                    stdout=json.dumps(self.docker_payload(plan_date="2026-08-17")),
                    stderr="",
                ),
            )
            self.assertEqual(path.read_text(encoding="utf-8"), "{not json")
            status = json.loads(
                (share / "last_enever_run.json").read_text(encoding="utf-8")
            )
            self.assertFalse(status["planned_profit"]["ok"])
            self.assertIn("malformed", status["planned_profit"]["error"])

    def test_locks_tomorrow_when_forecast_and_docker_rows_exist(self):
        completed_at = datetime(2026, 8, 16, 16, 30, tzinfo=CEST)
        forecast = [
            {"datetime": "2026-08-17T00:00:00+00:00", "electricity_price": 1},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            planned_profit.run_planned_profit_side_effect(
                share_dir=share,
                snapshot=self.snapshot(forecast=forecast),
                completed_at=completed_at,
                secrets_path=Path("/nonexistent/secrets.yaml"),
                docker_runner=lambda *args, **kwargs: MagicMock(
                    returncode=0,
                    stdout=json.dumps(self.docker_payload(plan_date="2026-08-17")),
                    stderr="",
                ),
            )
            record = planned_profit.day_record(share, date(2026, 8, 17))
            self.assertIsNotNone(record)
            self.assertEqual(record["planned_eur"], 1.0)

    @patch.object(planned_profit, "publish_ha_input_text")
    def test_publishes_today_record_after_lock(self, mock_publish):
        completed_at = datetime(2026, 8, 17, 10, 0, tzinfo=CEST)
        with tempfile.TemporaryDirectory() as tmp:
            share = Path(tmp)
            secrets = Path(tmp) / "secrets.yaml"
            secrets.write_text(
                "hass_url: http://ha.local:8123\nlong_lived_token: token\n",
                encoding="utf-8",
            )
            planned_profit.persist_lock(
                share,
                PersistLockTests.sample_record(plan_date="2026-08-17"),
                now=completed_at,
            )
            planned_profit.run_planned_profit_side_effect(
                share_dir=share,
                snapshot=self.snapshot(forecast=[]),
                completed_at=completed_at,
                secrets_path=secrets,
            )
        mock_publish.assert_called_once()
