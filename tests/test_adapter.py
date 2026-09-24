"""Model-free tests. A fake loopback model gateway plays the admitted
OpenAI-compatible binding (cog-qwen's shape, with its provenance record), and
TypeSafe's real system-one-adapter package runs end to end against it."""
import copy
import hashlib
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import adapter_runtime as rt      # noqa: E402
import system_one_contract as s1  # noqa: E402

TOKEN = "t" * 40
MODEL = "qwen3-4b-q4_k_m"
GOOD = {"answers": {"department": {"billing": 0.1, "technical": 0.8, "sales": 0.1},
                    "frustration": {"0": 0.1, "1": 0.7, "2": 0.2},
                    "is_urgent": 0.9}}


class Gateway:
    """A loopback gateway that answers each request with the next reply."""

    def __init__(self, replies, model=MODEL, provenance=True):
        self.replies, self.model, self.provenance, self.bodies = list(replies), model, provenance, []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                assert self.headers["Authorization"] == "Bearer " + TOKEN
                assert self.path == "/bindings/qwen-local/1/v1/chat/completions", self.path
                gateway.bodies.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                reply = gateway.replies.pop(0)
                body = {"id": "c1", "model": gateway.model,
                        "choices": [{"message": {"role": "assistant",
                                                 "content": reply if isinstance(reply, str) else json.dumps(reply)}}],
                        "usage": {"prompt_tokens": 120, "completion_tokens": 30}}
                if gateway.provenance:
                    body["cog_binding"] = {"binding_id": "qwen-local", "revision": 1,
                                           "outcome": "completed", "deviations": []}
                data = json.dumps(body).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server.server_port}"


def model_binding(base="http://127.0.0.1:9", locality="local", features=("text-generation", "json-output")):
    return {"document_kind": "binding", "contract": rt.CONTRACT, "binding_id": "qwen-local", "revision": 1,
            "state": "admitted", "provider": {"id": "openteams/cog-qwen", "version": "0.1.0"},
            "requirement_id": "model", "configuration": {"model_id": MODEL},
            "credential_refs": {"api_key": "env:COG_QWEN_BACKEND_TOKEN", "gateway_token": "env:COG_QWEN_TOKEN"},
            "capability": "model-endpoint/openai-compatible", "features": list(features),
            "composition": "model", "model": {"id": MODEL, "revision": None, "digest": None},
            "harness": None, "model_binding": None, "locality": locality,
            "invocation": {"protocol": "openai-chat-completions-v1",
                           "address": base + "/bindings/qwen-local/1/v1"},
            "qualification": {"level": "declaration", "identity_verified": False, "revision_pinned": False,
                              "evidence": [{"check": "test", "observation": "fixture"}]},
            "admission": {"resolver_id": "test-host", "checks": [{"check": "test", "passed": True, "detail": "fixture"}]}}


def admitted(binding):
    binding = copy.deepcopy(binding)
    binding["state"] = "admitted"
    binding["admission"] = {"resolver_id": "test-host", "checks": [
        {"check": "test", "passed": True, "detail": "mock admission"}]}
    return binding


class BindTests(unittest.TestCase):
    def setUp(self):
        self.request = json.loads((ROOT / "examples/bind-request.json").read_text())

    def test_candidate_is_never_admitted_and_names_the_adapter(self):
        result = rt.candidate(self.request)
        binding = result["binding"]
        self.assertEqual((binding["state"], binding["admission"]), ("candidate", None))
        self.assertEqual(binding["composition"], "harness")
        self.assertEqual(binding["harness"]["id"], "typesafe/system-one-adapter")
        self.assertEqual(binding["harness"]["version"], "0.2.1")
        self.assertEqual(binding["model_binding"], {"binding_id": "qwen-local", "revision": 1})
        self.assertEqual(binding["locality"], "local")
        rt.validate(result, "bind_result")
        self.assertEqual(rt.candidate(self.request), result)   # deterministic for host re-inspection

    def test_boundaries(self):
        cases = [(("requirement", "accepted_compositions"), ["model+harness"]),
                 (("requirement", "allowed_localities"), ["cloud"]),
                 (("requirement", "features"), ["json-output"]),
                 (("requirement", "evidence_level"), "probe"),
                 (("requirement", "identity_verified"), True),
                 (("requirement", "model_id"), MODEL),
                 (("configuration", "answer_mode"), "logprobs"),
                 (("configuration", "corrective_retries"), 9),
                 (("configuration", "tool_policy"), "vendor-restricted"),
                 (("credential_refs", "api_key"), "env:ANYTHING")]
        for (section, key), value in cases:
            request = copy.deepcopy(self.request)
            request[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(rt.Fault):
                rt.candidate(request)

    def test_studio_style_minimal_configuration_uses_defaults(self):
        request = copy.deepcopy(self.request)
        request["configuration"] = {"memory_mode": "none", "tool_policy": "no-tools",
                                    "model_binding": {"binding_id": "qwen-local", "revision": 1}}
        request["requirement"]["allowed_localities"] = ["cloud"]
        self.assertEqual(rt.candidate(request)["binding"]["locality"], "cloud")


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.binding = admitted(rt.candidate(json.loads((ROOT / "examples/bind-request.json").read_text()))["binding"])
        self.request = json.loads((ROOT / "examples/turn-request.json").read_text())
        self.env = patch.dict(os.environ, {"COG_QWEN_TOKEN": TOKEN})
        self.env.start()
        self.addCleanup(self.env.stop)

    def turn(self, gateway, binding=None, model=None, request=None):
        return rt.turn(request or self.request, binding or self.binding, model or model_binding(gateway.base))

    def test_the_real_adapter_answers_through_the_admitted_gateway(self):
        with Gateway([GOOD]) as gateway:
            response = self.turn(gateway)
        self.assertTrue(response["ok"])
        result = response["payload"]["result"]
        self.assertEqual((result["model"], result["answer_source"]), (MODEL, "llm-adapter"))
        self.assertEqual(s1.result_problems(result, self.request["task"]["questions"]), [])
        department = result["answers"]["department"]
        self.assertEqual(department["choice"], "technical")
        self.assertAlmostEqual(department["confidence"], (0.8 - 1 / 3) / (2 / 3), places=6)
        self.assertAlmostEqual(result["answers"]["frustration"]["score"], 1.1, places=6)
        self.assertEqual(result["answers"]["is_urgent"], {"type": "noul", "noul": 0.9})
        self.assertEqual(result["usage"], {"input_tokens": 120, "output_tokens": 30})
        sent = gateway.bodies[0]
        self.assertEqual(sent["model"], MODEL)
        self.assertNotIn("response_format", sent)   # prompted JSON by default
        self.assertEqual(sent["temperature"], 0)
        self.assertEqual([m["role"] for m in sent["messages"]], ["system", "user"])
        self.assertIn("<document>", sent["messages"][1]["content"])
        observed = response["provider_observations"]
        self.assertEqual(observed["gateway"][0]["binding_id"], "qwen-local")
        rt.validate(response["payload"], "harness_turn_result")

    def test_trailing_text_after_a_complete_object_is_salvaged(self):
        with Gateway([json.dumps(GOOD) + "}"]) as gateway:
            response = self.turn(gateway)
        self.assertTrue(response["ok"])
        self.assertEqual(len(gateway.bodies), 1)
        self.assertEqual(response["provider_observations"]["salvaged_replies"], 1)

    def test_json_mode_asks_the_gateway_for_json(self):
        binding = copy.deepcopy(self.binding)
        binding["configuration"]["json_mode"] = True
        with Gateway([GOOD]) as gateway:
            self.turn(gateway, binding=binding)
        self.assertEqual(gateway.bodies[0]["response_format"], {"type": "json_object"})
        with self.assertRaises(rt.Fault):
            rt.turn(self.request, binding, model_binding(features=("text-generation",)))
        # json_mode is always satisfiable: the model requirement includes json-output.
        self.assertIn("json-output", rt.CARD["model_requirement"]["features"])

    def test_a_malformed_reply_gets_one_corrective_retry(self):
        with Gateway(["not json at all", GOOD]) as gateway:
            response = self.turn(gateway)
        self.assertTrue(response["ok"])
        self.assertEqual(len(gateway.bodies), 2)
        self.assertEqual(response["provider_observations"]["corrective_retries"], 1)

    def test_persistently_malformed_replies_fail(self):
        with Gateway(["nope", "still nope"]) as gateway, self.assertRaises(rt.Fault) as caught:
            self.turn(gateway)
        self.assertEqual(caught.exception.code, "provider-unavailable")

    def test_a_different_answering_model_is_refused(self):
        with Gateway([GOOD], model="some-other-model") as gateway, self.assertRaises(rt.Fault) as caught:
            self.turn(gateway)
        self.assertEqual(caught.exception.code, "identity-mismatch")

    def test_missing_gateway_provenance_is_refused(self):
        with Gateway([GOOD], provenance=False) as gateway, self.assertRaises(rt.Fault) as caught:
            self.turn(gateway)
        self.assertEqual(caught.exception.code, "qualification-failed")

    def test_only_admitted_loopback_gateways_with_their_own_token(self):
        remote = model_binding("http://model.example.invalid:8080")
        with self.assertRaises(rt.Fault):
            rt.turn(self.request, self.binding, remote)
        wrong_token = model_binding()
        wrong_token["credential_refs"]["gateway_token"] = "env:OPENROUTER_COG_TOKEN"
        with self.assertRaises(rt.Fault):
            rt.turn(self.request, self.binding, wrong_token)

    def test_locality_and_features_must_match_the_model(self):
        with self.assertRaises(rt.Fault):
            rt.turn(self.request, self.binding, model_binding(locality="cloud"))
        request = json.loads((ROOT / "examples/bind-request.json").read_text())
        request["configuration"]["structured_outputs"] = True
        with self.assertRaises(rt.Fault):
            rt.candidate(request)

    def test_adapter_version_drift_requires_rebinding(self):
        drifted = copy.deepcopy(self.binding)
        drifted["harness"]["version"] = "0.1.0"
        with self.assertRaises(rt.Fault) as caught:
            rt.turn(self.request, drifted, model_binding())
        self.assertEqual(caught.exception.code, "qualification-failed")

    def test_references_tools_and_threads(self):
        for key, value in (("tool_grant_refs", ["g"]), ("thread_ref", "t"),
                           ("model_binding", {"binding_id": "other", "revision": 1})):
            request = copy.deepcopy(self.request)
            request[key] = value
            with self.subTest(key=key), self.assertRaises(rt.Fault):
                rt.turn(request, self.binding, model_binding())


class PackageTests(unittest.TestCase):
    def test_package_check(self):
        self.assertEqual(rt.package_check()["adapter_version"], "0.2.1")

    def test_vendored_contracts_match_recorded_digests(self):
        readme = (ROOT / "contracts/README.md").read_text()
        for path in (ROOT / "contracts/satisfier-binding.schema.json", ROOT / "src/system_one_contract.py"):
            with self.subTest(path=path.name):
                self.assertIn(hashlib.sha256(path.read_bytes()).hexdigest(), readme)


if __name__ == "__main__":
    unittest.main()
