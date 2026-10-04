import importlib.machinery
import importlib.util
import json
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
    ):
        registry = Mock(prefix="ghcr.io/unownhash/golbat", tag="main")
        digest = remote_digest or self.candidate_digest
        registry.candidate.return_value = (digest, {digest})
        operations = []

        def run(command, **kwargs):
            operations.append(command)
            if failing_pull and command[:2] == ["docker", "pull"]:
                raise RuntimeError("Download failed")
            if failing_notification and command[0] == "/usr/bin/python3":
                raise RuntimeError("Notification failed")
            return ""

        compose_path, compose_service, configured_policy = updater.SERVICES[
            service_name
        ]
        with patch.object(updater, "STATE_DIR", self.state_dir), patch.object(
            updater, "RECOVERY_OVERRIDE", self.state_dir / "recovery.staged.json"
        ), patch.dict(
            updater.SERVICES,
            {
                service_name: (
                    compose_path,
                    compose_service,
                    policy or configured_policy,
                )
            },
        ), patch.object(
            updater, "Registry", return_value=registry
        ), patch.object(
            updater,
            "inspect_container",
            side_effect=[self.before, after or self.before],
        ), patch.object(
            updater, "inspect_image", return_value=self.local_image
        ), patch.object(
            updater,
            "compose",
            return_value=json.dumps(
                {
                    "services": {
                        compose_service: {"image": self.before["Config"]["Image"]}
                    }
                }
            ),
        ), patch.object(
            updater, "run", side_effect=run
        ), patch.object(
            updater.shutil, "disk_usage", return_value=Mock(free=10 * 1024**3)
        ), patch.object(
            updater.sys, "argv", ["scanner-update", "--services", service_name, *args]
        ):
            result = updater.main()
        return result, operations

    def test_database_updates_are_downloaded_but_excluded_from_recovery(self):
        for name in ("db", "diadem-db"):
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
        result, operations = self.main_with_registry([])
        self.assertEqual(result, 0)
        reference = "ghcr.io/unownhash/golbat:main@" + self.candidate_digest
        self.assertIn(["docker", "pull", reference], operations)
        self.assertTrue(
            all(
                cmd[:3] in (["docker", "image", "tag"], ["docker", "pull", reference])
                for cmd in operations
            )
        )
        # Every tag operation uses a new staging/previous tag, never the live tag.
        self.assertTrue(
            all(
                cmd[-1].startswith(("scanner-staged/", "scanner-previous/"))
                for cmd in operations
                if cmd[:3] == ["docker", "image", "tag"]
            )
        )
        override = json.loads((self.state_dir / "unonwhash.staged.json").read_text())
        self.assertEqual(override, {"services": {"golbat": {"image": reference}}})

    def test_check_does_not_write_pull_tag_or_notify(self):
        result, operations = self.main_with_registry(["--check"])
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
        self.assertEqual(operations, [])
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
        self.assertEqual(operations, [])
        self.assertEqual(
            json.loads((self.state_dir / "unonwhash.staged.json").read_text()),
            {"services": {}},
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
