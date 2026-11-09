"""Production manifest wiring.

The api and worker have never run in production: neither Secret their pods read
exists yet. Wired anyway, every pod sat in CreateContainerConfigError and the
ExternalSecret failed every refresh, so the Argo Application stayed Degraded from
onboarding (2026-09-05) and could not signal a real fault. Until the launch steps in
README «Launching the api and worker» are done, those three manifests are spec only.
The launch PR uncomments them in the kustomization and flips the first test below, in
the same change.
"""

import pathlib

import yaml

PROD = pathlib.Path(__file__).resolve().parents[1] / "infra" / "k8s" / "production"
SPEC_ONLY = ("api-deployment.yaml", "worker-deployment.yaml", "external-secret.yaml")
# Written out-of-band by the Enclii postgres addon, never by a manifest here. Its
# existence is a launch precondition the operator checks; this test cannot.
ADDON_SECRETS = {"tlacuilo-secrets"}


def _resources() -> list[str]:
    return yaml.safe_load((PROD / "kustomization.yaml").read_text())["resources"]


def _docs(name: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((PROD / name).read_text()) if d]


def test_api_worker_and_external_secret_are_not_wired_until_launch():
    resources = _resources()
    for name in SPEC_ONLY:
        assert name not in resources, f"{name} is spec only until launch (README)"


def test_spec_only_manifests_stay_in_the_repo_and_say_why():
    for name in SPEC_ONLY:
        text = (PROD / name).read_text()
        assert "SPEC ONLY, NOT WIRED" in text, name
        assert _docs(name), name


def test_landing_stays_wired():
    resources = _resources()
    for name in ("web-deployment.yaml", "web-service.yaml", "network-policies.yaml"):
        assert name in resources


def test_wired_workloads_read_only_secrets_a_wired_resource_produces():
    # A non-optional Secret reference whose producer is not wired leaves the pod in
    # CreateContainerConfigError forever. Image pull secrets are platform-provisioned.
    # Wiring the api or worker without external-secret.yaml fails here.
    resources = _resources()
    docs = [d for name in resources for d in _docs(name)]
    produced = {
        d["spec"]["target"]["name"] for d in docs if d.get("kind") == "ExternalSecret"
    } | {d["metadata"]["name"] for d in docs if d.get("kind") == "Secret"} | ADDON_SECRETS
    for d in docs:
        if d.get("kind") != "Deployment":
            continue
        for c in d["spec"]["template"]["spec"]["containers"]:
            refs = [e["secretRef"] for e in c.get("envFrom", []) if "secretRef" in e]
            refs += [
                e["valueFrom"]["secretKeyRef"]
                for e in c.get("env", [])
                if "secretKeyRef" in e.get("valueFrom", {})
            ]
            for ref in refs:
                if ref.get("optional"):
                    continue
                assert ref["name"] in produced, (
                    f"{d['metadata']['name']} reads Secret {ref['name']} that no wired resource produces"
                )


def test_spec_only_deployments_launch_at_one_replica():
    # The Application ignores /spec/replicas on live Deployments, so the value here
    # only matters when Argo first creates the object at launch.
    for name in ("api-deployment.yaml", "worker-deployment.yaml"):
        (dep,) = _docs(name)
        assert dep["spec"]["replicas"] == 1
