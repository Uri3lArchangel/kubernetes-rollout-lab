import json
from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

from app import make_server
from lab import DEPLOYMENT, scenario, verify


class ServiceTests(unittest.TestCase):
    def exercise(self, ready):
        with make_server("127.0.0.1", 0, "candidate", ready) as server:
            worker = threading.Thread(target=server.serve_forever)
            worker.start()
            try:
                base = f"http://127.0.0.1:{server.server_port}"
                results = {}
                for path in ["/livez", "/readyz", "/version", "/missing"]:
                    try:
                        response = urlopen(base + path, timeout=2)
                    except HTTPError as rejected:
                        response = rejected
                    with response:
                        results[path] = (response.code, json.load(response))
                return results
            finally:
                server.shutdown()
                worker.join(3)

    def test_healthy_release(self):
        self.assertEqual(self.exercise(True), {
            "/livez": (200, {"alive": True}), "/readyz": (200, {"ready": True}),
            "/version": (200, {"release": "candidate"}),
            "/missing": (404, {"error": "not found"})
        })

    def test_unready_release_remains_live(self):
        report = self.exercise(False)
        self.assertEqual(report["/livez"], (200, {"alive": True}))
        self.assertEqual(report["/readyz"], (503, {"ready": False}))


class FakeKube:
    def __init__(self, mode):
        self.mode = mode
        self.release = "stable"
        self.calls = []
        self.served = []

    def smoke(self, kube, release):
        assert kube is self
        served = "candidate" if self.release == "fault" else self.release
        assert served == release
        self.served.append(release)

    def __call__(self, *argv, **kwargs):
        self.calls.append((argv, kwargs))
        response = type("Response", (), {"returncode": 0, "stdout": ""})()
        if argv[0] == "patch":
            template = json.loads(argv[-1])["spec"]["template"]
            self.release = template["metadata"]["labels"]["release"]
            self.assert_correct_env(template)
        if argv[:2] == ("rollout", "status") and self.release == "fault":
            response.returncode = 0 if self.mode == "unexpected-good" else 1
        if argv[:2] == ("rollout", "undo"):
            assert argv == ("rollout", "undo", DEPLOYMENT, "--to-revision=2")
            self.release = "candidate" if self.mode != "incorrect-revert" else "wrong"
        if argv[0] == "get":
            response.stdout = json.dumps({
                "metadata": {"generation": 3, "annotations": {
                    "deployment.kubernetes.io/revision": "2"}},
                "spec": {"template": {"metadata": {"labels": {
                    "release": self.release}}}},
                "status": {"observedGeneration": 3 if self.mode != "stale" else 2,
                           "conditions": [{"type": "Progressing", "status": "False",
                                           "reason": "ProgressDeadlineExceeded"
                                           if self.mode != "other-failure" else "Unknown"}]}})
        return response

    def assert_correct_env(self, template):
        assert template["spec"]["containers"][0]["env"] == [
            {"name": "READY", "value": "false" if self.release == "fault" else "true"}]


class ScenarioTests(unittest.TestCase):
    def test_validation_good_bad_and_revert(self):
        kube = FakeKube("normal")
        scenario(kube, kube.smoke)
        self.assertEqual(kube.calls[0][0][:3], ("apply", "--dry-run=server", "--validate=strict"))
        self.assertEqual(kube.served, ["stable", "candidate", "candidate", "candidate"])

    def test_unexpected_success_aborts(self):
        kube = FakeKube("unexpected-good")
        with self.assertRaisesRegex(RuntimeError, "unexpectedly became ready"):
            scenario(kube, kube.smoke)
        self.assertNotIn("undo", [argv[1] for argv, _ in kube.calls])

    def test_other_failure_does_not_count_as_expected_fault(self):
        for mode in ("other-failure", "stale"):
            with self.subTest(mode=mode):
                kube = FakeKube(mode)
                with self.assertRaisesRegex(RuntimeError, "reason other than"):
                    scenario(kube, kube.smoke)

    def test_undo_target_checked_against_actual_template(self):
        kube = FakeKube("incorrect-revert")
        with self.assertRaisesRegex(RuntimeError, "did not restore"):
            scenario(kube, lambda *_: None)

    def test_only_ready_endpoints_are_probed(self):
        urls = []
        def kube(*argv, **kwargs):
            if argv[0] == "get":
                entries = [
                    {"addresses": [ip], "targetRef": {"name": name}, "conditions": {"ready": ready}}
                    for ip, name, ready in [("10.1.1.1", "one", True), ("fd00::2", "two", True),
                                             ("10.1.1.3", "three", False)]]
                return SimpleNamespace(stdout=json.dumps({"items": [{"endpoints": entries}]}))
            self.assertEqual(argv[:2], ("exec", "one"))
            urls.append(argv[-1])
            return SimpleNamespace(stdout="candidate\n", returncode=0)
        verify(kube, "candidate")
        self.assertEqual(urls, ["http://release-api/version", "http://10.1.1.1:8080/version",
                                "http://[fd00::2]:8080/version"])

    def test_wrong_endpoint_version_fails(self):
        def kube(*argv, **kwargs):
            if argv[0] == "get":
                entries = [{"addresses": ["10.1.1.1"], "targetRef": {"name": "one"},
                            "conditions": {"ready": True}}]
                return SimpleNamespace(stdout=json.dumps({"items": [{"endpoints": entries}]}))
            return SimpleNamespace(stdout="stable\n" if "10.1.1.1" in argv[-1]
                                   else "candidate\n", stderr="", returncode=0)
        with patch("lab.time.sleep"), patch("lab.time.monotonic", side_effect=[0, 1, 36]):
            with self.assertRaisesRegex(RuntimeError, "Smoke check failed"):
                verify(kube, "candidate", 1)
