"""Create an isolated kind cluster and demonstrate gated deployment/rollback."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parent
NAME = "release-api"
DEPLOYMENT = f"deployment/{NAME}"
IMAGE = "rollout-lab:1"
FIXTURES = ("deployment.json", "service.json")
HTTP_CHECK = """import json, sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=3) as response:
    print(json.load(response)['release'])"""


def run(*argv, capture=False, check=True):
    print("+", subprocess.list2cmdline(list(argv)), flush=True)
    return subprocess.run(argv, text=True, capture_output=capture, check=check, timeout=360,
                          cwd=ROOT)


def verify(kube, expected, count=2):
    """Wait for exactly count routable endpoints and verify every pod and the Service."""
    max_wait = time.monotonic() + 35
    last = "no endpoints"
    while time.monotonic() < max_wait:
        slices = kube("get", "endpointslices", "-l", f"kubernetes.io/service-name={NAME}",
                      "-o", "json", capture=True)
        endpoints = [(ep["targetRef"]["name"], ep["addresses"][0])
                     for sl in json.loads(slices.stdout)["items"] for ep in sl["endpoints"]
                     if ep.get("conditions", {}).get("ready") is True]
        if len(endpoints) == count:
            source = endpoints[0][0]
            urls = [f"http://{NAME}/version"]
            for _, ip in endpoints:
                host = f"[{ip}]" if ":" in ip else ip
                urls.append(f"http://{host}:8080/version")
            results = [kube("exec", source, "--", "python", "-c", HTTP_CHECK, url,
                            capture=True, check=False) for url in urls]
            if all(result.returncode == 0 and result.stdout.strip() == expected
                   for result in results):
                print(f"Verified Service and {count} endpoints serve {expected}.")
                return
            last = str([(result.returncode, result.stdout.strip(), result.stderr.strip())
                        for result in results])
        else:
            last = f"expected {count} ready endpoints, observed {len(endpoints)}"
        time.sleep(1)
    raise RuntimeError(f"Smoke check failed for {expected}: {last}")


def patch(kube, release, ready):
    # Strategic merge preserves the Downward API RELEASE variable in this container.
    payload = {"spec": {"template": {"metadata": {"labels": {"release": release}},
                                    "spec": {"containers": [{"name": "api", "env": [
                                        {"name": "READY", "value": str(ready).lower()}]}]}}}}
    kube("patch", DEPLOYMENT, "--type=strategic", "-p", json.dumps(payload))


def deployment(kube):
    return json.loads(kube("get", DEPLOYMENT, "-o", "json", capture=True).stdout)


def scenario(kube, smoke=verify):
    kube("apply", "--dry-run=server", "--validate=strict",
         *[value for item in FIXTURES for value in ("-f", item)])
    kube("apply", *[value for item in FIXTURES for value in ("-f", item)])
    kube("rollout", "status", DEPLOYMENT, "--timeout=70s")
    smoke(kube, "stable")

    patch(kube, "candidate", True)
    kube("rollout", "status", DEPLOYMENT, "--timeout=70s")
    smoke(kube, "candidate")
    revision = deployment(kube)["metadata"]["annotations"]["deployment.kubernetes.io/revision"]

    patch(kube, "fault", False)
    status = kube("rollout", "status", DEPLOYMENT, "--timeout=65s", check=False)
    if status.returncode == 0:
        raise RuntimeError("Broken release unexpectedly became ready")
    failed = deployment(kube)
    conditions = failed["status"]["conditions"]
    if (failed["status"]["observedGeneration"] != failed["metadata"]["generation"]
            or not any(c["type"] == "Progressing" and c["status"] == "False"
                       and c["reason"] == "ProgressDeadlineExceeded" for c in conditions)):
        raise RuntimeError("Rollout failed for a reason other than the expected readiness stall")

    smoke(kube, "candidate")
    kube("rollout", "undo", DEPLOYMENT, f"--to-revision={revision}")
    kube("rollout", "status", DEPLOYMENT, "--timeout=70s")
    smoke(kube, "candidate")
    if deployment(kube)["spec"]["template"]["metadata"]["labels"]["release"] != "candidate":
        raise RuntimeError("Rollback did not restore the expected template")


def main():
    for binary in ("docker", "kind", "kubectl"):
        if not shutil.which(binary):
            raise SystemExit(f"Missing prerequisite: {binary}")
    cluster = f"rollout-lab-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="rollout-kubeconfig-") as base:
        config = os.fspath(Path(base) / "config")
        def kube(*argv, **kwargs):
            return run("kubectl", "--kubeconfig", config, *argv, **kwargs)

        run("docker", "build", "-t", IMAGE, ".")
        try:
            run("kind", "create", "cluster", "--name", cluster, "--kubeconfig", config,
                "--wait", "120s")
            run("kind", "load", "docker-image", IMAGE, "--name", cluster)
            scenario(kube)
        except (RuntimeError, subprocess.SubprocessError):
            if Path(config).exists():
                try:
                    kube("describe", DEPLOYMENT, check=False)
                    kube("get", "pods,replicasets", "-o", "wide", check=False)
                except subprocess.SubprocessError as error:
                    print(f"Could not collect diagnostics: {error}")
            raise
        finally:
            cleanup = run("kind", "delete", "cluster", "--name", cluster, check=False)
            if cleanup.returncode:
                print(f"Cleanup failed; run: kind delete cluster --name {cluster}")


if __name__ == "__main__":
    main()
