# Kubernetes Rollout Lab

Run a successful rolling update, deliberately stall a second release at readiness, and then restore the last verified revision. The lab checks the HTTP response from the Kubernetes Service and every ready endpoint. Its aim is to make rollout and rollback behavior observable without relying on a real environment or registry.

## Run

Prerequisites: Python 3.10+, Docker with a running daemon, kind, and kubectl. Kubectl should be within one minor version of the kind node's Kubernetes version. Run from the repository root:

```sh
python3 lab.py
```

The script builds a local image, creates a uniquely named single-node cluster with a temporary kubeconfig, loads the image, performs a strict API-server dry run, and runs the scenario. It deletes the cluster in a cleanup step. No existing application context is used. If a run is killed before cleanup, use the cluster name printed in the command log: `kind delete cluster --name <name>`. The local image remains cached.

## What the scenario verifies

1. **Baseline:** deploy two replicas and check that the Service and both ready endpoints return `stable`.
2. **Healthy update:** set the Pod template's release label to `candidate` and check that traffic reaches the updated replicas. Record the successful Deployment revision.
3. **Readiness failure:** publish a Pod template that answers `200` on `/livez` but `503` on `/readyz`. Require `ProgressDeadlineExceeded` on the current Deployment generation; any other failure is not a successful experiment.
4. **Rollback:** check that the Service and its ready endpoints still return `candidate`. Undo to the recorded revision, then verify the restored template and HTTP responses again.

With two stable replicas, `maxUnavailable: 0`, and `maxSurge: 1`, an unready third replica should stall the rollout without replacing an available replica. The smoke checks are snapshots, not a continuous traffic or zero-downtime guarantee. The rollout intentionally changes template configuration rather than changing binaries so that image pulls cannot confound the readiness experiment.

## Design notes

| Choice | Why / trade-off |
| --- | --- |
| Image built in two stages with tests in the first stage | The runtime receives only the service code; the runtime still includes the base Python image. |
| Restricted Pod security | Numeric unprivileged UID/GID, no service-account token, read-only root, default seccomp, and all Linux capabilities dropped. This is not an assertion of full production hardening. |
| Independent liveness and readiness | A Pod that is running but cannot receive requests should not enter Service endpoints or be restarted just for being unready. |
| Explicit rollback target | The lab restores a revision that passed smoke checks, not whichever revision happened to be previous. |
| Local image and single node | Avoids registry deployment and isolates variables; it does not model a registry, node failures, or rolling data migrations. |

## Verification

```sh
python3 -m unittest -v
# Optional: install jsonschema in your local Python environment for structural checks.
python3 validate_schema.py
```

Pass `--cache PATH` to use previously downloaded upstream schemas instead of making a network request.

Eight tests passed in the authoring environment: real localhost HTTP behavior and simulated kubectl success/failure/undo and EndpointSlice handling. Both manifests passed structural checks against upstream Kubernetes v1.36.0 OpenAPI. Docker and a Kubernetes cluster were unavailable there, so image build, strict server-side dry run, and cluster behavior were not verified in that environment. The included GitHub Actions workflow runs `python3 lab.py` on a real kind cluster; check the workflow result before citing an integration pass.

References: [Kubernetes Deployments](https://kubernetes.io/docs/concepts/workloads/controllers/deployment/) · [kind Quick Start](https://kind.sigs.k8s.io/docs/user/quick-start/).

Portfolio lab. Not a production deployment.
