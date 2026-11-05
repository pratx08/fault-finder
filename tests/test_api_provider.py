"""The public API adapter is opt-in and treats provider output as untrusted data."""

import json
import os
import subprocess
import sys

import httpx
import pytest
import typer

from faultfinder.agents import CompatibleAPIClient, Review
from faultfinder.cli import model_client
from faultfinder.discovery import inspect_project

ENDPOINT = "https://api.example.test/v1/chat/completions"


def test_compatible_api_request_and_validated_response():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"explanation": "No control was provided."})
                        },
                    }
                ]
            },
        )

    client = CompatibleAPIClient("chosen-model", ENDPOINT, "test-key", httpx.MockTransport(respond))
    try:
        assert client.review({"measurement": "bounded"}) == "No control was provided."
    finally:
        client.close()

    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == ENDPOINT
    assert request.headers["authorization"] == "Bearer test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "chosen-model"
    assert payload["stream"] is False
    assert payload["max_tokens"] == 2500
    assert "test-key" not in request.content.decode()
    assert json.loads(payload["messages"][1]["content"]) == {"measurement": "bounded"}


def test_compatible_api_plans_against_real_source(tmp_path):
    (tmp_path / "subject.py").write_text(
        "class Owner:\n    def __init__(self):\n        self.items = []\n"
        "    def run(self):\n        self.items.append(object())\n"
    )
    answers = iter(
        [
            {
                "explanation": "Owner retains items",
                "target_file": "subject.py",
                "target_line": 5,
                "expected_lifetime": "Unknown",
            },
            {
                "code": "def build(variant, track):\n    return object()\n",
                "control_explanation": "Compare retention disabled",
            },
        ]
    )
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(next(answers))}}
                ]
            },
        )

    client = CompatibleAPIClient("chosen-model", ENDPOINT, "test-key", httpx.MockTransport(respond))
    try:
        plan = client.plan(inspect_project(tmp_path), tmp_path)
    finally:
        client.close()
    assert plan.kind == "probe"
    assert plan.model == "api:api.example.test:chosen-model"
    assert len(requests) == 2
    assert "self.items.append" in requests[0].content.decode()
    assert all("test-key" not in request.content.decode() for request in requests)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.example/v1/chat/completions",
        "https://user:secret@example.com/v1/chat/completions",
        "https://example.com/v1/chat/completions?token=secret",
        "https://example.com/v1/responses",
    ],
)
def test_unsafe_or_incompatible_endpoint_rejected(endpoint):
    with pytest.raises(ValueError, match="HTTPS Chat Completions"):
        CompatibleAPIClient("model", endpoint, "key")


def test_missing_key_fails_before_network(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        CompatibleAPIClient("model", ENDPOINT)


@pytest.mark.parametrize(
    "provider_body",
    [
        {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "not JSON"}}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": '{"verdict":"verified"}'}}]},
    ],
)
def test_provider_cannot_turn_malformed_output_into_evidence(provider_body):
    client = CompatibleAPIClient(
        "model",
        ENDPOINT,
        "key",
        httpx.MockTransport(lambda _: httpx.Response(200, json=provider_body)),
    )
    try:
        with pytest.raises(ValueError):
            client.ask("Review evidence", {}, Review)
    finally:
        client.close()


def test_provider_error_does_not_print_remote_body():
    client = CompatibleAPIClient(
        "model",
        ENDPOINT,
        "key",
        httpx.MockTransport(lambda _: httpx.Response(401, text="secret provider details")),
    )
    try:
        with pytest.raises(ValueError, match="HTTP 401") as error:
            client.ask("Review evidence", {}, Review)
        assert "secret provider details" not in str(error.value)
    finally:
        client.close()


def test_api_mode_requires_model_endpoint_and_explicit_provider(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "key")
    with pytest.raises(typer.BadParameter, match="requires --api-url and --model"):
        model_client("api", "", ENDPOINT, "LLM_API_KEY")
    with pytest.raises(typer.BadParameter, match="requires --provider api"):
        model_client("ollama", "", ENDPOINT, "LLM_API_KEY")
    with pytest.raises(typer.BadParameter, match="Provider must"):
        model_client("unknown", "model", ENDPOINT, "LLM_API_KEY")


def test_custom_provider_key_environment(monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("PROVIDER_API_KEY", "provider-key")
    client = model_client("api", "chosen-model", ENDPOINT, "PROVIDER_API_KEY")
    assert isinstance(client, CompatibleAPIClient)
    client.close()


def test_cli_reports_missing_key_without_starting_investigation(tmp_path):
    environment = os.environ.copy()
    environment.pop("LLM_API_KEY", None)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "faultfinder",
            "check",
            str(tmp_path),
            "--provider",
            "api",
            "--api-url",
            ENDPOINT,
            "--model",
            "chosen-model",
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 2
    assert "LLM_API_KEY" in result.stdout + result.stderr
    assert "Traceback" not in result.stdout + result.stderr
