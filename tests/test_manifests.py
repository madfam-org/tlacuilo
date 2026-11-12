"""Production manifest wiring.

The api and worker have never run in production: neither Secret their pods read
exists yet. Wired anyway, every pod sat in CreateContainerConfigError and the
ExternalSecret failed every refresh, so the Argo Application stayed Degraded from
onboarding (2026-09-05) and could not signal a real fault. Until the launch steps in
README «Launching the api and worker» are done, those three manifests are spec only.

Two launches, two flags. The sync-only launch wires the api alone (it starts with the
database Secret; its queue Secret is optional): that change uncomments
`api-deployment.yaml` in the kustomization and flips API_LAUNCHED, nothing else. The
full launch later wires the worker and the ExternalSecret and flips QUEUE_LAUNCHED.
"""

import pathlib

import yaml

PROD = pathlib.Path(__file__).resolve().parents[1] / "infra" / "k8s" / "production"
API_LAUNCHED = False  # the sync-only launch flips this line
QUEUE_LAUNCHED = False  # the full launch (worker and ExternalSecret) flips this one
API = ("api-deployment.yaml",)
QUEUE = ("worker-deployment.yaml", "external-secret.yaml")
KEPT_IN_REPO = (*API, *QUEUE)
SPEC_ONLY = (() if API_LAUNCHED else API) + (() if QUEUE_LAUNCHED else QUEUE)
# Written out-of-band by the Enclii postgres addon, never by a manifest here. Its
# existence is a launch precondition the operator checks; this test cannot.
ADDON_SECRETS = {"tlacuilo-secrets"}


def _resources() -> list[str]:
    return yaml.safe_load((PROD / "kustomization.yaml").read_text())["resources"]


def _docs(name: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((PROD / name).read_text()) if d]


def _secret_refs(deployment: dict) -> list[dict]:
    refs = []
    for c in deployment["spec"]["template"]["spec"]["containers"]:
        refs += [e["secretRef"] for e in c.get("envFrom", []) if "secretRef" in e]
        refs += [e["valueFrom"]["secretKeyRef"] for e in c.get("env", []) if "secretKeyRef" in e.get("valueFrom", {})]
    return refs


def _unproduced_secrets(resources: list[str]) -> list[tuple[str, str]]:
    """(Deployment, Secret) for every required Secret that no listed resource produces.
    A non-optional reference whose producer is not wired leaves the pod in
    CreateContainerConfigError forever. Image pull secrets are platform-provisioned."""
    docs = [d for name in resources for d in _docs(name)]
    produced = (
        {d["spec"]["target"]["name"] for d in docs if d.get("kind") == "ExternalSecret"}
        | {d["metadata"]["name"] for d in docs if d.get("kind") == "Secret"}
        | ADDON_SECRETS
    )
    return [
        (d["metadata"]["name"], ref["name"])
        for d in docs
        if d.get("kind") == "Deployment"
        for ref in _secret_refs(d)
        if not ref.get("optional") and ref["name"] not in produced
    ]


def test_api_worker_and_external_secret_are_not_wired_until_launch():
    resources = _resources()
    for name in SPEC_ONLY:
        assert name not in resources, f"{name} is spec only until launch (README)"
    assert ("api-deployment.yaml" in resources) is API_LAUNCHED
    for name in QUEUE:
        assert (name in resources) is QUEUE_LAUNCHED


def test_spec_only_manifests_stay_in_the_repo_and_say_why():
    for name in KEPT_IN_REPO:
        text = (PROD / name).read_text()
        assert "SPEC ONLY, NOT WIRED" in text, name
        assert _docs(name), name


def test_landing_stays_wired():
    resources = _resources()
    for name in ("web-deployment.yaml", "web-service.yaml", "network-policies.yaml"):
        assert name in resources


def test_wired_workloads_read_only_secrets_a_wired_resource_produces():
    # Wiring the worker without external-secret.yaml fails here.
    assert _unproduced_secrets(_resources()) == []


def test_the_api_starts_with_the_database_secret_alone():
    # A1-D4, the sync-only launch: the queue URLs, the gateway token and the callback
    # key are deferred, so the Secret that carries them must not block the pod.
    (api,) = _docs("api-deployment.yaml")
    refs = {ref["name"]: bool(ref.get("optional")) for ref in _secret_refs(api)}
    assert refs == {"tlacuilo-secrets": False, "tlacuilo-config": True}
    assert {name for name, optional in refs.items() if not optional} <= ADDON_SECRETS


def test_wiring_the_api_alone_needs_no_external_secret_and_the_worker_still_does():
    resources = [r for r in _resources() if r not in KEPT_IN_REPO]
    assert _unproduced_secrets([*resources, "api-deployment.yaml"]) == []
    assert _unproduced_secrets([*resources, "worker-deployment.yaml"]) == [("tlacuilo-worker", "tlacuilo-config")]
    assert _unproduced_secrets([*resources, *KEPT_IN_REPO]) == []
    (worker,) = _docs("worker-deployment.yaml")
    assert not any(ref.get("optional") for ref in _secret_refs(worker))  # no queue, no worker


def test_consumer_ingress_admits_a_closed_list_of_namespaces_and_only_the_api_port():
    (policy,) = [d for d in _docs("network-policies.yaml") if d["metadata"]["name"] == "allow-consumer-ingress"]
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app.kubernetes.io/name": "tlacuilo-api"}
    (rule,) = policy["spec"]["ingress"]
    (peer,) = rule["from"]
    (expr,) = peer["namespaceSelector"]["matchExpressions"]
    assert (expr["key"], expr["operator"]) == ("kubernetes.io/metadata.name", "In")
    assert sorted(expr["values"]) == [
        "crea-map",
        "dhanam",
        "enclii-dhanam-staging",
        "enclii-karafiel-staging",
        "karafiel",
    ]
    assert rule["ports"] == [{"protocol": "TCP", "port": 8000}]


def test_spec_only_deployments_launch_at_one_replica():
    # The Application ignores /spec/replicas on live Deployments, so the value here
    # only matters when Argo first creates the object at launch.
    for name in ("api-deployment.yaml", "worker-deployment.yaml"):
        (dep,) = _docs(name)
        assert dep["spec"]["replicas"] == 1
