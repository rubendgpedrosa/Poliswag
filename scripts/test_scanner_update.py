import importlib.machinery
import importlib.util
import json
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

SOURCE = Path(__file__).with_name("scanner-update")
loader = importlib.machinery.SourceFileLoader("scanner_update", str(SOURCE))
spec = importlib.util.spec_from_loader(loader.name, loader)
updater = importlib.util.module_from_spec(spec)
loader.exec_module(updater)


class StagingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state_dir = Path(self.directory.name)
        self.before = {
            "Id": "container-original",
            "Image": "sha256:" + "a" * 64,
            "Config": {"Image": "ghcr.io/unownhash/golbat:main"},
            "State": {"StartedAt": "2026-10-02T00:00:00Z"},
            "RestartCount": 0,
        }
        self.local_image = {
            "Id": self.before["Image"],
            "Architecture": "amd64",
            "Os": "linux",
            "RepoDigests": ["ghcr.io/unownhash/golbat@" + self.before["Image"]],
        }
        self.candidate_digest = "sha256:" + "b" * 64

    def main_with_registry(
        self,
        args,
        remote_digest=None,
        after=None,
        failing_pull=False,
        policy=None,
        failing_notification=False,
        service_name="golbat",
        listings=None,
        listing_errors=None,
        removal_errors=None,
        state=None,
        created=None,
        inspect_errors=None,
        service_names=None,
    ):
        names = service_names or (service_name,)
        registry = Mock(prefix="ghcr.io/unownhash/golbat", tag="main")
        digest = remote_digest or self.candidate_digest
        registry.candidate.return_value = (digest, {digest})
        operations = []

        def run(command, **kwargs):
            operations.append(command)
            if command[:3] == ["docker", "image", "ls"]:
                if listing_errors and command[-1] in listing_errors:
                    raise listing_errors[command[-1]]
                return "\n".join((listings or {}).get(command[-1], []))
            if command[:3] == ["docker", "image", "rm"]:
                if removal_errors and command[3] in removal_errors:
                    raise removal_errors[command[3]]
                return ""
            if failing_pull and command[:2] == ["docker", "pull"]:
                raise RuntimeError("Download failed")
            if failing_notification and command[0] == "/usr/bin/python3":
                raise RuntimeError("Notification failed")
            return ""

        def inspect(reference):
            if inspect_errors and reference in inspect_errors:
                raise inspect_errors[reference]
            if created and reference in created:
                return {"Created": created[reference]}
            return self.local_image

        entries = {
            name: (
                updater.SERVICES[name][0],
                updater.SERVICES[name][1],
                policy or updater.SERVICES[name][2],
            )
            for name in names
        }
        configured = {
            updater.SERVICES[name][1]: {"image": self.before["Config"]["Image"]}
            for name in names
        }
        inspections = []
        for _ in names:
            inspections.extend([self.before, after or self.before])
        if state is not None:
            (self.state_dir / "state.json").write_text(json.dumps(state))
        with patch.object(updater, "STATE_DIR", self.state_dir), patch.object(
            updater, "RECOVERY_OVERRIDE", self.state_dir / "recovery.staged.json"
        ), patch.dict(
            updater.SERVICES,
            entries,
        ), patch.object(
            updater, "Registry", return_value=registry
        ), patch.object(
            updater,
            "inspect_container",
            side_effect=inspections,
        ), patch.object(
            updater, "inspect_image", side_effect=inspect
        ), patch.object(
            updater,
            "compose",
            return_value=json.dumps({"services": configured}),
        ), patch.object(
            updater, "run", side_effect=run
        ), patch.object(
            updater.shutil, "disk_usage", return_value=Mock(free=10 * 1024**3)
        ), patch.object(
            updater.sys, "argv", ["scanner-update", "--services", *names, *args]
        ):
            try:
                result = updater.main()
            except Exception as error:
                result = error
        return result, operations

    def test_database_updates_are_downloaded_but_excluded_from_recovery(self):
        for name in ("db",):
            with self.subTest(service=name):
                result, operations = self.main_with_registry(
                    ["--notify"], service_name=name
                )
                self.assertEqual(result, 0)
                self.assertTrue(
                    any(cmd[:2] == ["docker", "pull"] for cmd in operations)
                )
                self.assertFalse(
                    any(
                        cmd[:2] in (["docker", "restart"], ["docker", "compose"])
                        for cmd in operations
                    )
                )
                path, compose_service, _ = updater.SERVICES[name]
                override = json.loads(
                    (self.state_dir / (path.parent.name + ".staged.json")).read_text()
                )
                self.assertIn(compose_service, override["services"])
                recovery = json.loads(
                    (self.state_dir / "recovery.staged.json").read_text()
                )
                self.assertNotIn(compose_service, recovery["services"])
                notice = next(cmd for cmd in operations if cmd[0] == "/usr/bin/python3")
                self.assertIn(
                    "quando o servidor for desligado ou reiniciado", notice[2]
                )

    def test_stage_downloads_immutable_digest_and_writes_separate_override(self):
        staged = "scanner-staged/golbat:" + "b" * 12
        rollback = "scanner-previous/golbat:" + "a" * 12
        stale_staged = "scanner-staged/golbat:" + "1" * 12
        stale_rollback = "scanner-previous/golbat:" + "2" * 12
        result, operations = self.main_with_registry(
            [],
            listings={
                "scanner-staged/golbat": [staged, stale_staged],
                "scanner-previous/golbat": [rollback, stale_rollback],
            },
        )
        self.assertEqual(result, 0)
        reference = "ghcr.io/unownhash/golbat:main@" + self.candidate_digest
        self.assertIn(["docker", "pull", reference], operations)

        def scoped(tag):
            return tag.startswith(("scanner-staged/", "scanner-previous/"))

        def is_allowed(command):
            if command[:2] == ["docker", "pull"]:
                return command == ["docker", "pull", reference]
            if command[:3] == ["docker", "image", "tag"]:
                return (
                    len(command) == 5
                    and command[3] in (reference, self.before["Image"])
                    and scoped(command[4])
                )
            if command[:3] == ["docker", "image", "ls"]:
                return (
                    len(command) == 6
                    and command[3:5] == ["--format", "{{.Repository}}:{{.Tag}}"]
                    and scoped(command[5])
                )
            if command[:3] == ["docker", "image", "rm"]:
                return len(command) == 4 and scoped(command[3])
            return False

        # Only pull, staging tags, a read-only listing and scoped tag removal;
        # never a write to the live tag or a Compose deploy.
        self.assertTrue(all(is_allowed(cmd) for cmd in operations))
        # Every tag operation uses a new staging/previous tag, never the live tag.
        self.assertEqual(
            {cmd[-1] for cmd in operations if cmd[:3] == ["docker", "image", "tag"]},
            {staged, rollback},
        )
        # Pruning removes the superseded tags and keeps the current ones.
        self.assertEqual(
            {cmd[3] for cmd in operations if cmd[:3] == ["docker", "image", "rm"]},
            {stale_staged, stale_rollback},
        )
        override = json.loads((self.state_dir / "unonwhash.staged.json").read_text())
        self.assertEqual(override, {"services": {"golbat": {"image": reference}}})

    def test_check_does_not_write_pull_tag_or_notify(self):
        result, operations = self.main_with_registry(["--check"])
        self.assertEqual(result, 0)
        self.assertEqual(operations, [])
        self.assertEqual(list(self.state_dir.iterdir()), [])

    def test_check_never_prunes_tags(self):
        listings = {
            "scanner-staged/golbat": ["scanner-staged/golbat:" + "1" * 12],
            "scanner-previous/golbat": ["scanner-previous/golbat:" + "2" * 12],
        }
        result, operations = self.main_with_registry(["--check"], listings=listings)
        self.assertEqual(result, 0)
        self.assertEqual(operations, [])
        self.assertEqual(list(self.state_dir.iterdir()), [])

    def test_notification_is_sent_once_until_pending_updates_change(self):
        result, operations = self.main_with_registry(["--notify"])
        self.assertEqual(result, 0)
        notices = [cmd for cmd in operations if cmd[0] == "/usr/bin/python3"]
        self.assertEqual(len(notices), 1)
        self.assertIn("golbat", notices[0][2])
        self.assertEqual(notices[0][3], "📦 Atualizações prontas a instalar")
        result, operations = self.main_with_registry(["--notify"])
        self.assertEqual(result, 0)
        self.assertFalse(any(cmd[0] == "/usr/bin/python3" for cmd in operations))
        result, operations = self.main_with_registry(
            ["--notify"], remote_digest="sha256:" + "c" * 64
        )
        self.assertEqual(result, 0)
        self.assertEqual(sum(cmd[0] == "/usr/bin/python3" for cmd in operations), 1)

    def test_failed_notification_is_retried_on_next_run(self):
        result, _ = self.main_with_registry(["--notify"], failing_notification=True)
        self.assertEqual(result, 1)
        self.assertNotIn(
            "notified_signature",
            json.loads((self.state_dir / "state.json").read_text()),
        )
        result, operations = self.main_with_registry(["--notify"])
        self.assertEqual(result, 0)
        self.assertEqual(sum(cmd[0] == "/usr/bin/python3" for cmd in operations), 1)

    def test_check_rejects_notification_option(self):
        with self.assertRaises(SystemExit) as error:
            self.main_with_registry(["--check", "--notify"])
        self.assertEqual(error.exception.code, 2)

    def test_report_only_never_downloads_or_stages(self):
        result, operations = self.main_with_registry([], policy="report")
        self.assertEqual(result, 0)
        self.assertEqual(operations, [])
        self.assertEqual(
            json.loads((self.state_dir / "unonwhash.staged.json").read_text()),
            {"services": {}},
        )

    def test_applied_override_is_retained_for_future_recovery(self):
        result, _ = self.main_with_registry([])
        self.assertEqual(result, 0)
        previous = json.loads((self.state_dir / "unonwhash.staged.json").read_text())
        self.before["Image"] = self.candidate_digest
        self.local_image["Id"] = self.candidate_digest
        self.local_image["RepoDigests"] = [
            "ghcr.io/unownhash/golbat@" + self.candidate_digest
        ]
        result, operations = self.main_with_registry([])
        self.assertEqual(result, 0)
        # Pruning may only list tags; it must not pull, tag or rewrite Compose.
        self.assertTrue(all(cmd[:3] == ["docker", "image", "ls"] for cmd in operations))
        self.assertEqual(
            json.loads((self.state_dir / "unonwhash.staged.json").read_text()), previous
        )
        self.assertFalse(
            json.loads((self.state_dir / "state.json").read_text())["services"][
                "golbat"
            ]["pending"]
        )

    def test_current_image_does_not_pull_or_stage(self):
        result, operations = self.main_with_registry([], self.before["Image"])
        self.assertEqual(result, 0)
        # The only expected operation is the read-only tag listing for pruning.
        self.assertTrue(all(cmd[:3] == ["docker", "image", "ls"] for cmd in operations))
        self.assertEqual(
            json.loads((self.state_dir / "unonwhash.staged.json").read_text()),
            {"services": {}},
        )

    def test_rollback_is_replaced_image_and_survives_run_without_download(self):
        def records():
            state = json.loads((self.state_dir / "state.json").read_text())
            return state["services"]

        result, _ = self.main_with_registry([])
        self.assertEqual(result, 0)
        self.assertEqual(
            records()["golbat"]["rollback"], "scanner-previous/golbat:" + "a" * 12
        )
        # The next download replaces the running candidate and records it.
        self.before["Image"] = self.candidate_digest
        self.local_image["Id"] = self.candidate_digest
        self.local_image["RepoDigests"] = [
            "ghcr.io/unownhash/golbat@" + self.candidate_digest
        ]
        successor = "sha256:" + "c" * 64
        result, _ = self.main_with_registry([], remote_digest=successor)
        self.assertEqual(result, 0)
        self.assertEqual(
            records()["golbat"]["rollback"], "scanner-previous/golbat:" + "b" * 12
        )
        # A later run that only finds the running image keeps that rollback.
        self.before["Image"] = successor
        self.local_image["Id"] = successor
        self.local_image["RepoDigests"] = ["ghcr.io/unownhash/golbat@" + successor]
        result, operations = self.main_with_registry([], remote_digest=successor)
        self.assertEqual(result, 0)
        self.assertFalse(any(cmd[:2] == ["docker", "pull"] for cmd in operations))
        self.assertEqual(
            records()["golbat"]["rollback"], "scanner-previous/golbat:" + "b" * 12
        )

    def test_legacy_record_adopts_newest_previous_tag(self):
        older = "scanner-previous/golbat:" + "1" * 12
        newer = "scanner-previous/golbat:" + "2" * 12
        state = {"services": {"golbat": {"candidate": "legacy-candidate"}}}
        result, operations = self.main_with_registry(
            [],
            remote_digest=self.before["Image"],
            state=state,
            listings={"scanner-previous/golbat": [older, newer]},
            created={
                older: "2026-10-01T00:00:00.000000000Z",
                newer: "2026-10-02T00:00:00.000000000Z",
            },
        )
        self.assertEqual(result, 0)
        record = json.loads((self.state_dir / "state.json").read_text())["services"][
            "golbat"
        ]
        self.assertEqual(record["rollback"], newer)
        removed = {
            command[3]
            for command in operations
            if command[:3] == ["docker", "image", "rm"]
        }
        self.assertEqual(removed, {older})

    def test_legacy_rollback_that_cannot_be_inspected_prunes_nothing(self):
        older = "scanner-previous/golbat:" + "1" * 12
        newer = "scanner-previous/golbat:" + "2" * 12
        result, operations = self.main_with_registry(
            [],
            remote_digest=self.before["Image"],
            state={"services": {"golbat": {"candidate": "legacy-candidate"}}},
            listings={"scanner-previous/golbat": [older, newer]},
            inspect_errors={older: RuntimeError("inspect failed")},
        )
        self.assertEqual(result, 0)
        record = json.loads((self.state_dir / "state.json").read_text())["services"][
            "golbat"
        ]
        self.assertNotIn("rollback", record)
        self.assertFalse(
            any(command[:3] == ["docker", "image", "rm"] for command in operations)
        )

    def test_failed_download_is_not_marked_ready(self):
        result, operations = self.main_with_registry([], failing_pull=True)
        self.assertEqual(result, 1)
        self.assertEqual(len(operations), 1)
        self.assertEqual(
            json.loads((self.state_dir / "unonwhash.staged.json").read_text()),
            {"services": {}},
        )

    def test_detects_external_container_restart(self):
        after = dict(self.before, State={"StartedAt": "2026-10-02T01:00:00Z"})
        result, _ = self.main_with_registry([], after=after)
        self.assertEqual(result, 1)
        self.assertEqual(
            json.loads((self.state_dir / "unonwhash.staged.json").read_text()),
            {"services": {}},
        )

    def test_fingerprint_change_skips_pruning_and_saved_record(self):
        stale = "scanner-staged/golbat:" + "1" * 12
        original = {"golbat": {"candidate": "previous-candidate"}}
        after = dict(self.before, State={"StartedAt": "2026-10-02T01:00:00Z"})
        result, operations = self.main_with_registry(
            [],
            after=after,
            listings={"scanner-staged/golbat": [stale]},
            state={"services": original},
        )
        self.assertEqual(result, 1)
        self.assertFalse(
            any(command[:3] == ["docker", "image", "rm"] for command in operations)
        )
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["services"], original)

    def test_state_or_override_write_failure_skips_all_pruning(self):
        stale = "scanner-staged/golbat:" + "1" * 12
        for target in ("save_overrides", "atomic_json"):
            with self.subTest(target=target), patch.object(
                updater, target, side_effect=RuntimeError("write failed")
            ):
                result, operations = self.main_with_registry(
                    [], listings={"scanner-staged/golbat": [stale]}
                )
                self.assertIsInstance(result, RuntimeError)
                self.assertFalse(
                    any(
                        command[:3] == ["docker", "image", "rm"]
                        for command in operations
                    )
                )

    def test_docker_mutation_guard_rejects_restart_and_deploy(self):
        for operation in ("up", "down", "restart", "stop", "start"):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                updater.run(
                    ["docker", "compose", "-f", "/any/file.yml", operation, "config"]
                )
        for command in (
            ["docker", "restart", "dragonite"],
            ["docker", "rm", "dragonite"],
            ["docker", "exec", "dragonite", "sh"],
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                updater.run(command)

    def test_image_removal_guard_is_limited_to_its_own_tags(self):
        for command in (
            ["docker", "image", "rm", "golbat:main"],
            ["docker", "image", "rm", "ghcr.io/unownhash/golbat:main"],
            ["docker", "image", "rm"],
            ["docker", "image", "rm", "scanner-staged/golbat:abc", "--force"],
            ["docker", "image", "rm", "scanner-staged/golbat:abc", "extra"],
            ["docker", "image", "rm", "--force", "scanner-staged/golbat:abc"],
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                updater.run(command)
        completed = Mock(returncode=0, stdout="")
        with patch.object(updater.subprocess, "run", return_value=completed) as execute:
            for command in (
                ["docker", "image", "rm", "scanner-staged/golbat:abc"],
                ["docker", "image", "rm", "scanner-previous/golbat:abc"],
                [
                    "docker",
                    "image",
                    "ls",
                    "--format",
                    "{{.Repository}}:{{.Tag}}",
                    "scanner-staged/golbat",
                ],
            ):
                with self.subTest(command=command):
                    self.assertEqual(updater.run(command), "")
        self.assertEqual(execute.call_count, 3)

    def test_prune_tags_keeps_requested_tags_and_continues_after_failure(self):
        keep = "scanner-staged/golbat:" + "b" * 12
        stale = "scanner-staged/golbat:" + "1" * 12
        busy = "scanner-staged/golbat:" + "2" * 12
        removed = []
        messages = []

        def run(command, **kwargs):
            if command[:3] == ["docker", "image", "ls"]:
                self.assertEqual(command[-1], "scanner-staged/golbat")
                return "\n".join((keep, stale, busy))
            self.assertEqual(command[:3], ["docker", "image", "rm"])
            if command[3] == busy:
                raise RuntimeError("image is being used by a container")
            removed.append(command[3])
            return ""

        with patch.object(updater, "run", side_effect=run), patch.object(
            updater, "log", side_effect=messages.append
        ):
            updater.prune_tags("scanner-staged/golbat", {keep})
        self.assertEqual(removed, [stale])
        self.assertTrue(
            any(message.startswith("removed " + stale) for message in messages)
        )
        self.assertTrue(any(message.startswith("kept " + busy) for message in messages))

    def test_prune_tags_logs_timeout_and_value_error_and_continues(self):
        keep = "scanner-staged/golbat:" + "b" * 12
        stale = "scanner-staged/golbat:" + "1" * 12
        for operation, error in (
            ("ls", subprocess.TimeoutExpired(["docker", "image", "ls"], 60)),
            ("ls", ValueError("command outside staging policy")),
            ("rm", subprocess.TimeoutExpired(["docker", "image", "rm"], 60)),
            ("rm", ValueError("command outside staging policy")),
        ):
            with self.subTest(operation=operation, error=type(error).__name__):
                removed = []
                messages = []

                def run(command, **kwargs):
                    if command[:3] == ["docker", "image", "ls"]:
                        if operation == "ls":
                            raise error
                        return stale
                    self.assertEqual(command[:3], ["docker", "image", "rm"])
                    if operation == "rm":
                        raise error
                    removed.append(command[3])
                    return ""

                with patch.object(updater, "run", side_effect=run), patch.object(
                    updater, "log", side_effect=messages.append
                ):
                    updater.prune_tags("scanner-staged/golbat", {keep})
                self.assertEqual(removed, [])
                if operation == "ls":
                    self.assertTrue(
                        any(
                            message.startswith("could not list scanner-staged/golbat")
                            for message in messages
                        )
                    )
                else:
                    self.assertTrue(
                        any(message.startswith("kept " + stale) for message in messages)
                    )

    def test_prune_failures_do_not_abort_other_services(self):
        stale = "scanner-staged/golbat:" + "1" * 12
        rollback = "scanner-previous/golbat:" + "9" * 12
        messages = []
        with patch.object(updater, "log", side_effect=messages.append):
            result, operations = self.main_with_registry(
                [],
                remote_digest=self.before["Image"],
                service_names=("golbat", "admin"),
                state={"services": {"golbat": {"rollback": rollback}}},
                listings={"scanner-staged/golbat": [stale]},
                listing_errors={
                    "scanner-previous/golbat": ValueError("guard rejected")
                },
                removal_errors={
                    stale: subprocess.TimeoutExpired(["docker", "image", "rm"], 60)
                },
            )
        self.assertEqual(result, 0)
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(set(state["services"]), {"golbat", "admin"})
        self.assertEqual(state["errors"], [])
        self.assertTrue(
            any(message.startswith("kept " + stale) for message in messages)
        )
        self.assertTrue(
            any(
                message.startswith("could not list scanner-previous/golbat")
                for message in messages
            )
        )
        self.assertIn(
            [
                "docker",
                "image",
                "ls",
                "--format",
                "{{.Repository}}:{{.Tag}}",
                "scanner-staged/admin",
            ],
            operations,
        )

    def test_testing_version_uses_numeric_order_and_never_downgrades(self):
        registry = object.__new__(updater.Registry)
        registry.base, registry.repo = "https://ghcr.io", "unownhash/dragonite-public"
        registry.tag = "dragonite-v1.20.9-testing"
        registry.get = Mock(
            return_value=(
                {
                    "tags": [
                        "dragonite-v1.20.9-testing",
                        "dragonite-v1.20.16-testing",
                        "latest",
                        "dragonite-v1.21.0",
                    ]
                },
                "",
                "",
            )
        )
        self.assertEqual(registry.newest_testing(), "dragonite-v1.20.16-testing")
        registry.tag = "dragonite-v1.20.20-testing"
        self.assertEqual(registry.newest_testing(), "dragonite-v1.20.20-testing")
        registry.tag = "latest"
        with self.assertRaises(RuntimeError):
            registry.newest_testing()

    def test_single_component_docker_hub_tag_is_not_a_hostname(self):
        response = Mock()
        response.read.return_value = b'{"token": "anonymous-registry-token"}'
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(updater.urllib.request, "urlopen", return_value=response):
            registry = updater.Registry("mariadb:10.11")
        self.assertEqual(registry.host, "docker.io")
        self.assertEqual(registry.repo, "library/mariadb")
        self.assertEqual(registry.tag, "10.11")
        self.assertEqual(registry.prefix, "mariadb")

    def test_registry_checks_running_platform_in_multiarch_index(self):
        registry = object.__new__(updater.Registry)
        registry.base, registry.repo = "https://ghcr.io", "unownhash/golbat"
        registry.get = Mock(
            side_effect=[
                (
                    {
                        "manifests": [
                            {
                                "platform": {"architecture": "arm64", "os": "linux"},
                                "digest": "sha256:arm",
                            },
                            {
                                "platform": {"architecture": "amd64", "os": "linux"},
                                "digest": "sha256:amd",
                            },
                        ]
                    },
                    "sha256:index",
                    "",
                ),
                ({"config": {"digest": "sha256:config"}}, "sha256:amd", ""),
            ]
        )
        digest, matches = registry.candidate("main", "amd64", "linux")
        self.assertEqual(digest, "sha256:index")
        self.assertEqual(matches, {"sha256:index", "sha256:amd", "sha256:config"})


if __name__ == "__main__":
    unittest.main()
