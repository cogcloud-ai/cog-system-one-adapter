"""System One turns answered by an LLM through TypeSafe's System One adapter.

A harness provider for `system-one/decisions`. It owns no model: an
independently admitted `model-endpoint/openai-compatible` binding (local
cog-qwen, or cog-openrouter) supplies inference through its loopback gateway.
TypeSafe's MIT-licensed `system-one-adapter` builds the prompt and output
schema, decodes and normalises the LLM's probabilities and computes
confidence; `GatewayProvider` below is the only transport it is given, so
every model call goes to the admitted gateway and carries its provenance.

The answers have System One's shape, not its meaning. They are LLM-stated
probabilities (`answer_source: llm-adapter`), not calibrated decisions. Use
this for local or offline work, for comparison with Jev, and where state must
not leave the machine; tune thresholds per source.

Provider half only: `bind` returns a candidate and never admits it.
"""
import argparse
import copy
import hashlib
import http.client
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

sys.path.insert(0, str(Path(__file__).resolve().parent))
import system_one_contract as s1  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CARD = json.loads((ROOT / "binding/provider.json").read_text())
SCHEMA = json.loads((ROOT / "contracts/satisfier-binding.schema.json").read_text())
CONTRACT = CARD["contract"]
IDENTITY = CARD["provider"]
COMPOSITION = "harness"
HARNESS_ID = "typesafe/system-one-adapter"
FEATURES = frozenset(s1.QUESTION_TYPES)
MAX_BYTES = 8 * 1024 * 1024
TURN_SECONDS = 600
DEFAULTS = {"locality": "cloud", "answer_mode": "probabilities", "json_mode": False,
            "corrective_retries": 1}
# The model gateways this harness may call, and the one credential reference
# each accepts. Same allowlist as cog-turn-harness.
GATEWAY_TOKENS = {"openteams/cog-openrouter": "env:OPENROUTER_COG_TOKEN",
                  "openteams/cog-qwen": "env:COG_QWEN_TOKEN"}


class Fault(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(detail)


def require(condition, code, detail):
    if not condition:
        raise Fault(code, detail)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate(value, definition=None, schema=None):
    if schema is None:
        schema = dict(SCHEMA, **{"$ref": "#/$defs/" + definition})
        schema.pop("oneOf", None)
    try:
        errors = list(Draft202012Validator(schema).iter_errors(value))
    except SchemaError:
        raise Fault("invalid-configuration", "Declared schema is not valid JSON Schema.") from None
    if errors:
        where = "/".join(str(p) for p in errors[0].absolute_path) or "$"
        raise Fault("invalid-configuration", f"Invalid {definition or 'document'} at {where}; rule {errors[0].validator}.")


def envelope(task, payload=None, fault=None, binding=None):
    return {"envelope": 1, "cog": dict(IDENTITY), "task": task, "ok": fault is None,
            "error": {"code": fault.code, "detail": str(fault)} if fault else None,
            "payload": payload, "raw": None,
            "problems": [{"check": fault.code, "detail": str(fault), "severity": "error"}] if fault else [],
            "binding": binding, "timing": {"latency_s": None}}


def adapter_version():
    return importlib.metadata.version("system-one-adapter")


def settings(config):
    return {**DEFAULTS, **{k: v for k, v in config.items() if k in DEFAULTS}}


# ------------------------------------------------------------------ bind --

def candidate(request):
    """Bind request -> candidate harness binding (never admitted here). The
    host checks the referenced model binding against `model_requirement`."""
    require(isinstance(request, dict) and request.get("contract") == CONTRACT,
            "unsupported-contract", "Unsupported binding contract.")
    validate(request, "bind_request")
    require(request["provider"] == IDENTITY, "incompatible-requirement", "Provider identity mismatch.")
    validate(request["configuration"], schema=CARD["configuration_schema"])
    validate(request["credential_refs"], schema=CARD["credential_schema"])
    req, config = request["requirement"], request["configuration"]
    options = settings(config)
    require(req["capability"] == CARD["capability"] and COMPOSITION in req["accepted_compositions"],
            "incompatible-requirement", "This provider supplies system-one/decisions as a Harness only.")
    require(set(req["features"]) <= FEATURES, "incompatible-requirement",
            f"Supported features are the question types {sorted(FEATURES)}.")
    require(options["locality"] in req["allowed_localities"], "incompatible-requirement",
            "The configured locality (which must match the admitted model) is not permitted.")
    require(not req["identity_verified"] and not req["revision_pinned"], "qualification-failed",
            "Model identity is attested (or not) by the separate model binding, never by this harness.")
    require(req["evidence_level"] == "declaration", "qualification-failed",
            "This harness advertises declaration evidence only.")
    require(req["model_id"] is None, "incompatible-requirement",
            "Put model identity constraints on the separate model requirement.")
    version = adapter_version()
    binding = {"document_kind": "binding", "contract": CONTRACT, "binding_id": request["binding_id"],
               "revision": request["revision"], "state": "candidate", "provider": dict(IDENTITY),
               "requirement_id": req["id"], "configuration": config,
               "credential_refs": request["credential_refs"], "capability": CARD["capability"],
               "features": req["features"], "composition": COMPOSITION, "model": None,
               "harness": {"id": HARNESS_ID, "version": version,
                           "configuration_digest": "sha256:" + digest(config)},
               "model_binding": config["model_binding"], "locality": options["locality"],
               "invocation": {"protocol": "cog-harness-turn-command-v1", "address": "cog-command:turn"},
               "qualification": {"level": "declaration", "identity_verified": False, "revision_pinned": False,
                                 "evidence": [{"check": "adapter-controls",
                                               "observation": f"system-one-adapter {version} asks the separately admitted "
                                                              f"model for {options['answer_mode']} answers; no tools or "
                                                              f"memory. LLM-stated, not calibrated."}]},
               "admission": None}
    validate(binding, "binding")
    return {"document_kind": "bind_result", "contract": CONTRACT, "request_id": request["request_id"],
            "status": "candidate", "binding": binding, "problems": []}


# ------------------------------------------------------------------ turn --

def check_turn(request, binding, model_binding):
    validate(binding, "binding")
    require(binding["state"] == "admitted" and binding["provider"] == IDENTITY
            and binding["composition"] == COMPOSITION and binding["capability"] == CARD["capability"],
            "unauthorized", "An admitted system-one/decisions binding for this provider is required.")
    require(binding["harness"]["version"] == adapter_version(), "qualification-failed",
            "The installed system-one-adapter differs from the admitted one; rebind.")
    validate(request, "harness_turn_request")
    ref = {"binding_id": binding["binding_id"], "revision": binding["revision"]}
    require(request["binding"] == ref and request["model_binding"] == binding["model_binding"],
            "incompatible-requirement", "Turn binding references do not match.")
    require(not request["tool_grant_refs"] and request["thread_ref"] is None,
            "incompatible-requirement", "System One turns take no tools and no remembered thread.")
    require(isinstance(model_binding, dict), "incompatible-requirement", "The admitted model binding is required.")
    validate(model_binding, "binding")
    require(model_binding["state"] == "admitted" and model_binding["composition"] == "model"
            and model_binding["capability"] == "model-endpoint/openai-compatible",
            "incompatible-requirement", "The separate model must be an admitted OpenAI-compatible Model binding.")
    require({"binding_id": model_binding["binding_id"], "revision": model_binding["revision"]}
            == binding["model_binding"], "incompatible-requirement", "Separate model reference mismatch.")
    require(model_binding["locality"] == binding["locality"], "incompatible-requirement",
            "Harness locality differs from its model binding.")
    options = settings(binding["configuration"])
    needed = {"text-generation", "json-output"} if options["json_mode"] else {"text-generation"}
    require(needed <= set(model_binding["features"]), "incompatible-requirement",
            f"The model binding lacks features {sorted(needed - set(model_binding['features']))}.")
    try:
        task = s1.check_task(request["task"])
    except ValueError as exc:
        raise Fault("invalid-configuration", str(exc)) from None
    used = {q["type"] for q in task["questions"].values()}
    require(used <= set(binding["features"]), "incompatible-requirement",
            f"The admitted binding does not include question types {sorted(used - set(binding['features']))}.")
    return task


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class GatewayProvider:
    """The adapter's SyncProvider protocol over a host-admitted loopback model
    gateway. One call per `request`; the adapter owns retries and decoding.
    Each completed call must carry the gateway's own provenance for exactly
    the admitted model binding."""

    def __init__(self, model_binding, deadline, opener=None, json_mode=False):
        address = model_binding["invocation"]["address"]
        parsed = urlsplit(address)
        require(parsed.scheme == "http" and parsed.hostname == "127.0.0.1" and parsed.port
                and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
                "unauthorized", "Only a host-admitted loopback model gateway is accepted.")
        require(model_binding["invocation"]["protocol"] == "openai-chat-completions-v1",
                "incompatible-requirement", "Unsupported model protocol.")
        reference = model_binding["credential_refs"].get("gateway_token")
        require(reference is not None and reference == GATEWAY_TOKENS.get(model_binding["provider"]["id"]),
                "unauthorized", "Unsupported model provider or gateway credential reference.")
        token = os.environ.get(reference[4:], "")
        require(bool(token) and "\n" not in token and "\r" not in token,
                "missing-credential", "The model gateway credential is unavailable.")
        self.binding = model_binding
        self.address = address.rstrip("/") + "/chat/completions"
        self.token = token
        self.deadline = deadline
        self.opener = opener or urllib.request.build_opener(NoRedirect)
        self.model_name = model_binding["model"]["id"]
        self.json_mode = json_mode
        self.gateway_records = []
        self.salvaged = 0

    def request(self, messages, *, schema, structured):
        from system_one_adapter.providers import ProviderResult
        body = {"model": self.model_name, "temperature": 0,
                "messages": [{"role": m.role, "content": m.content} for m in messages]}
        # Prompted output only: the adapter validates locally and re-asks.
        # (Gateway strict-schema mode turns a violation into an HTTP error,
        # which would bypass the adapter's corrective re-asks.)
        require(not structured, "invalid-configuration", "Structured output mode is not supported.")
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        left = self.deadline - time.monotonic()
        require(left > 0, "provider-unavailable", "The turn deadline passed before the model call.")
        req = urllib.request.Request(self.address, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": "Bearer " + self.token})
        try:
            with self.opener.open(req, timeout=left) as response:
                raw = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise Fault("provider-unavailable", f"Model gateway returned HTTP {status}.") from None
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            raise Fault("provider-unavailable", "Model gateway is unreachable.") from None
        require(len(raw) <= MAX_BYTES, "provider-unavailable", "Model response exceeded the size limit.")
        try:
            data = json.loads(raw)
            message = data["choices"][0]["message"]
            content = message["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise Fault("provider-unavailable", "Model gateway returned a malformed completion.") from None
        require(data.get("model") == self.model_name, "identity-mismatch",  # no echo of the reply
                "Model response identity differs from the admitted model binding.")
        require(not message.get("tool_calls") and isinstance(content, str), "provider-unavailable",
                "Model returned tool calls or no text content.")
        facts = data.get("cog_binding")
        require(isinstance(facts, dict) and facts.get("binding_id") == self.binding["binding_id"]
                and facts.get("revision") == self.binding["revision"] and facts.get("outcome") == "completed"
                and not facts.get("deviations"), "qualification-failed",
                "Missing or mismatched model gateway provenance.")
        self.gateway_records.append(facts)
        usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        tokens = [usage.get(k) if isinstance(usage.get(k), int) else None
                  for k in ("prompt_tokens", "completion_tokens")]
        return ProviderResult(text=self.leading_object(content), input_tokens=tokens[0],
                              output_tokens=tokens[1])

    def leading_object(self, content):
        """Small models sometimes follow a complete JSON object with stray
        text (local Qwen3-4B was observed adding one extra `}`). Pass the first
        complete object on and count the salvage; anything that is not a
        complete object passes through unchanged for the adapter to refuse and
        correct. The adapter still validates the object against its schema."""
        text = content.strip()
        if text.startswith("```"):
            return content          # the adapter strips code fences itself
        try:
            json.loads(text)
            return content
        except ValueError:
            pass
        start = text.find("{")
        if start < 0:
            return content
        try:
            value, end = json.JSONDecoder().raw_decode(text, start)
        except ValueError:
            return content
        if not isinstance(value, dict):
            return content
        self.salvaged += 1
        return text[start:end]

    def translate_error(self, error):
        from typesafe_sdk import TypeSafeError
        return error if isinstance(error, TypeSafeError) else TypeSafeError(str(error))


def contract_answers(dumped):
    """SDK answers (pydantic JSON dump) -> contract answers."""
    fields = {"noul": ("type", "noul"),
              "choice": ("type", "choice", "probabilities", "confidence"),
              "score": ("type", "score", "legend", "probabilities", "confidence")}
    out = {}
    for qid, answer in dumped.items():
        kind = answer.get("type")
        require(kind in fields, "provider-unavailable", f"Adapter returned an unrecognised answer for {qid}.")
        out[qid] = {k: answer[k] for k in fields[kind]}
        for key in ("probabilities", "legend"):
            if isinstance(out[qid].get(key), dict):
                out[qid][key] = {str(k): v for k, v in out[qid][key].items()}
    return out


def ask(task, binding, model_binding, deadline, opener=None):
    """One System One evaluation through the adapter -> (result, observations)."""
    from system_one_adapter import SystemOneAdapterClient
    from typesafe_sdk import TypeSafeError
    from pydantic import ValidationError
    options = settings(binding["configuration"])
    provider = GatewayProvider(model_binding, deadline, opener, json_mode=options["json_mode"])
    client = SystemOneAdapterClient(structured_outputs=False,
                                    llm_answer_mode=options["answer_mode"],
                                    normalize_probabilities=True,
                                    n_retry_malformed_structure=options["corrective_retries"])
    started = time.monotonic()
    try:
        response = client.system_one(copy.deepcopy(task["state"]), copy.deepcopy(task["questions"]), model=provider)
    except Fault:
        raise
    except TypeSafeError as exc:
        cause = exc.__cause__
        if isinstance(cause, Fault):
            raise cause from None
        raise Fault("provider-unavailable", "The model did not produce usable System One answers: "
                    + str(exc).split("\n")[0][:300]) from None
    except (ValidationError, ValueError) as exc:
        raise Fault("invalid-configuration", "The adapter refused the questions: " + str(exc).split("\n")[0][:300]) from None
    finally:
        client.close()
    dumped = response.model_dump(mode="json")
    usage = dumped.get("usage") or {}
    tokens = {"input_tokens": usage.get("input_tokens_total"), "output_tokens": usage.get("output_tokens_total")}
    tokens = {k: v if isinstance(v, int) and v >= 0 else None for k, v in tokens.items()}
    result = {"model": model_binding["model"]["id"], "answer_source": "llm-adapter",
              "answers": contract_answers(dumped["answers"]), "usage": tokens}
    problems = s1.result_problems(result, task["questions"])
    require(not problems, "qualification-failed", "Adapter answers do not fit the questions: " + "; ".join(problems[:3]))
    observations = {"answer_source": "llm-adapter", "adapter_version": binding["harness"]["version"],
                    "answer_mode": options["answer_mode"], "requested_model": model_binding["model"]["id"],
                    "identity_verified": False, "latency_s": round(time.monotonic() - started, 3),
                    "retries": usage.get("n_retries"),
                    "corrective_retries": usage.get("n_retries_malformed_structure"),
                    "salvaged_replies": provider.salvaged,
                    "gateway": provider.gateway_records}
    return result, observations


def turn(request, binding, model_binding=None, timeout=TURN_SECONDS, opener=None):
    deadline = time.monotonic() + timeout
    task = check_turn(request, binding, model_binding)
    result, observations = ask(task, binding, model_binding, deadline, opener)
    payload = {"document_kind": "harness_turn_result", "contract": CONTRACT, "request_id": request["request_id"],
               "binding": request["binding"], "model_binding": request["model_binding"],
               "result": result, "tool_uses": []}
    validate(payload, "harness_turn_result")
    response = envelope("turn", payload, binding=binding)
    response["provider_observations"] = observations
    return response


# ----------------------------------------------------------------- check --

def package_check():
    validate(CARD, "provider")
    for field in ("configuration_schema", "credential_schema"):
        Draft202012Validator.check_schema(CARD[field])
    import yaml
    manifest = yaml.safe_load((ROOT / "cog.yaml").read_text())
    require(manifest["id"] == IDENTITY["id"] and str(manifest["version"]) == IDENTITY["version"],
            "invalid-configuration", "Manifest and provider declaration identities differ.")
    require(CARD["capability"] == s1.CAPABILITY and CARD["compositions"] == [COMPOSITION],
            "invalid-configuration", "Declaration must offer system-one/decisions as a Harness.")
    import system_one_adapter  # noqa: F401  (the pinned dependency is installed)
    return {"package": "valid", "availability": "not-probed", "adapter_version": adapter_version(),
            "typesafe_sdk_version": importlib.metadata.version("typesafe-sdk")}


def read(path):
    p = Path(path)
    require(p.stat().st_size <= MAX_BYTES, "invalid-configuration", "Input document exceeds the size limit.")
    return json.loads(p.read_text())


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["card", "check", "bind", "turn", "validate"])
    parser.add_argument("--request")
    parser.add_argument("--binding")
    parser.add_argument("--model-binding")
    parser.add_argument("--definition")
    args = parser.parse_args(argv)
    try:
        if args.operation == "card":
            result = envelope("card", CARD)
        elif args.operation == "check":
            result = envelope("check", package_check())
        elif args.operation == "validate":
            validate(read(args.request), args.definition)
            result = envelope("validate", {"valid": True})
        elif args.operation == "bind":
            result = envelope("bind", candidate(read(args.request)))
        else:
            result = turn(read(args.request), read(args.binding),
                          read(args.model_binding) if args.model_binding else None)
    except Fault as exc:
        result = envelope(args.operation, fault=exc)
    except (OSError, ValueError, KeyError, TypeError):
        result = envelope(args.operation, fault=Fault("invalid-configuration",
                                                      "Invalid or unavailable local document."))
    try:
        text = json.dumps(result, indent=2, allow_nan=False)
    except ValueError:
        result = envelope(args.operation, fault=Fault("provider-unavailable", "Result contained non-finite numbers."))
        text = json.dumps(result, indent=2)
    print(text)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
