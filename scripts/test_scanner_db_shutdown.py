import gzip
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

loader = importlib.machinery.SourceFileLoader(
    "scanner_db_shutdown", str(Path(__file__).with_name("scanner-db-shutdown"))
)
spec = importlib.util.spec_from_loader(loader.name, loader)
hook = importlib.util.module_from_spec(spec)
loader.exec_module(hook)


class ShutdownTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)
        self.record = {
            "candidate": "mariadb:10.11@sha256:" + "b" * 64,
            "digest": "sha256:" + "b" * 64,
            "version": "10.11",
            "pending": True,
            "downloaded": True,
        }
        self.current = {"Image": "old-image", "State": {"Running": True}}
        self.updated = {
            "Image": "new-image",
            "State": {"Running": True},
            "Config": {"Image": self.record["candidate"]},
        }

    def main(self, args, shutdown=True, apply_error=None):
        (self.state_dir / "state.json").write_text(
            json.dumps({"services": {"db": self.record}})
        )
        with patch.object(hook, "STATE_DIR", self.state_dir), patch.object(
            hook.sys, "argv", ["scanner-db-shutdown", *args]
        ), patch.object(hook, "shutting_down", return_value=shutdown), patch.object(
            hook,
            "apply_database",
            return_value=(self.updated, "/backup.sql.gz"),
            side_effect=apply_error,
        ) as apply, patch.object(
            hook, "notify"
        ) as notify:
            result = hook.main()
        return result, apply, notify

    def test_normal_stop_of_hook_does_not_update_databases(self):
        result, apply, notify = self.main(["--shutdown"], shutdown=False)
        self.assertEqual(result, 0)
        apply.assert_not_called()
        notify.assert_not_called()

    def test_check_does_not_apply_or_notify(self):
        result, apply, notify = self.main(["--check"])
        self.assertEqual(result, 0)
        apply.assert_not_called()
        notify.assert_not_called()

    def test_shutdown_records_verified_application(self):
        result, apply, notify = self.main(["--shutdown"])
        self.assertEqual(result, 0)
        apply.assert_called_once()
        notify.assert_called_once()
        saved = json.loads((self.state_dir / "state.json").read_text())["services"][
            "db"
        ]
        self.assertFalse(saved["pending"])
        self.assertEqual(saved["running_image"], "new-image")
        self.assertEqual(saved["pre_update_backup"], "/backup.sql.gz")

    def test_failed_apply_remains_pending_and_notifies(self):
        result, _, notify = self.main(
            ["--shutdown"], apply_error=RuntimeError("Backup failed")
        )
        self.assertEqual(result, 1)
        saved = json.loads((self.state_dir / "state.json").read_text())["services"][
            "db"
        ]
        self.assertTrue(saved["pending"])
        self.assertEqual(saved["apply_error"], "Backup failed")
        self.assertEqual(notify.call_args.args[1], ["db: Backup failed"])

    def test_not_downloaded_is_never_applied(self):
        self.record["downloaded"] = False
        result, apply, _ = self.main(["--shutdown"])
        self.assertEqual(result, 0)
        apply.assert_not_called()

    def test_completed_update_is_not_reapplied(self):
        self.record["pending"] = False
        result, apply, _ = self.main(["--shutdown"])
        self.assertEqual(result, 0)
        apply.assert_not_called()

    def apply(
        self,
        *,
        running=True,
        already_applied=False,
        backup_error=None,
        health_error=None
    ):
        operations = []
        self.current["State"]["Running"] = running
        if already_applied:
            self.current["Image"] = "new-image"

        def run(command, **kwargs):
            operations.append(command)
            if command[-3:] == ["config", "--format", "json"]:
                return json.dumps({"services": {"db": {"image": "mariadb:10.11"}}})
            if "up" in command:
                override = json.loads(Path(command[5]).read_text())
                self.assertEqual(
                    override, {"services": {"db": {"image": self.record["candidate"]}}}
                )
            return ""

        with patch.object(hook, "shutting_down", return_value=True), patch.object(
            hook, "STATE_DIR", self.state_dir
        ), patch.object(hook, "run", side_effect=run), patch.object(
            hook,
            "inspect",
            side_effect=[self.current, {"Id": "new-image"}, self.updated],
        ), patch.object(
            hook, "backup", return_value="/backup.sql.gz", side_effect=backup_error
        ) as backup, patch.object(
            hook, "wait_ready", side_effect=health_error
        ) as ready:
            result = hook.apply_database("db", self.record)
        return result, operations, backup, ready

    def test_applies_only_database_using_downloaded_image(self):
        result, operations, backup, ready = self.apply()
        backup.assert_called_once_with("db")
        ready.assert_called_once_with("db")
        command = next(cmd for cmd in operations if "up" in cmd)
        self.assertEqual(command[-1], "db")
        self.assertIn("--no-deps", command)
        self.assertIn("--no-build", command)
        self.assertEqual(command[command.index("--pull") + 1], "never")
        self.assertEqual(
            operations[-1],
            ["docker", "image", "tag", self.record["candidate"], "mariadb:10.11"],
        )
        self.assertEqual(result[1], "/backup.sql.gz")

    def test_backup_failure_prevents_deployment(self):
        with self.assertRaisesRegex(RuntimeError, "Backup failed"):
            self.apply(backup_error=RuntimeError("Backup failed"))

    def test_already_stopped_database_is_not_started(self):
        with self.assertRaisesRegex(RuntimeError, "já estava parada"):
            self.apply(running=False)

    def test_existing_candidate_still_requires_readiness(self):
        _, operations, backup, ready = self.apply(already_applied=True)
        backup.assert_not_called()
        ready.assert_called_once_with("db")
        self.assertFalse(any("up" in cmd for cmd in operations))
        with self.assertRaisesRegex(RuntimeError, "Not ready"):
            self.apply(already_applied=True, health_error=RuntimeError("Not ready"))

    def test_wrong_version_line_rejected_before_docker(self):
        self.record["candidate"] = self.record["candidate"].replace("10.11", "12.0")
        with patch.object(hook, "run") as run, self.assertRaisesRegex(
            RuntimeError, "série de versões configurada"
        ):
            hook.apply_database("db", self.record)
        run.assert_not_called()

    def test_shutdown_targets_only(self):
        for jobs, expected in [
            ("1 reboot.target start waiting\n2 shutdown.target start waiting", True),
            ("3 docker.service stop running", False),
            ("4 scanner-db-shutdown.service stop running", False),
            ("5 shutdown.target stop waiting", False),
        ]:
            with self.subTest(jobs=jobs), patch.object(hook, "run", return_value=jobs):
                self.assertEqual(hook.shutting_down(), expected)

    def test_backup_requires_complete_dump(self):
        payload = (
            b"-- MariaDB dump\n"
            + b"data\n" * 100
            + b"-- Dump completed on 2026-10-02\n"
        )

        def dump(command, **kwargs):
            kwargs["output"].write(payload)

        with patch.object(hook, "BACKUP_DIR", self.state_dir), patch.object(
            hook.shutil, "disk_usage", return_value=Mock(free=10 * 1024**3)
        ), patch.object(hook, "run", side_effect=dump):
            saved = Path(hook.backup("db"))
            self.assertEqual(gzip.decompress(saved.read_bytes()), payload)
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            payload = b"truncated data" * 50
            with self.assertRaisesRegex(
                RuntimeError, "cópia de segurança ficou incompleta"
            ):
                hook.backup("diadem-db")
            self.assertEqual(list(self.state_dir.glob("diadem-db-*.sql.gz")), [])


if __name__ == "__main__":
    unittest.main()
