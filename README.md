# System One Adapter Cog

The second way to satisfy `system-one/decisions`. Where cog-typesafe sends
typed questions to TypeSafe's Jev, this package sends them to an LLM you have
already admitted in the suite — local Qwen through cog-qwen, or a hosted model
through cog-openrouter — using TypeSafe's own
[System One adapter](https://github.com/typesafe-ai/system-one-adapter-python).
Decision Cogs receive answers of the same shape either way, with
`answer_source` saying which kind they are.

| | cog-typesafe | cog-system-one-adapter |
|---|---|---|
| Answers from | Jev, a System One model | an LLM asked for typed probabilities |
| Confidence | calibrated (RLCD-trained) | derived from LLM-stated probabilities |
| Composition | `model+harness` | `harness` over a separate `model` binding |
| Locality | cloud (TypeSafe) | the model binding's: local with cog-qwen |
| Credential | `TYPESAFE_API_KEY` | the model gateway's token |
| `answer_source` | `system-one-model` | `llm-adapter` |

## Install and inspect

```sh
pixi install
pixi run check
pixi run test          # model-free: the real adapter against a fake gateway
pixi run card
```

## Bind over an admitted model

First admit a model binding in Workbench — for example cog-qwen's example
(`qwen-local`, revision 1, local) — and keep its gateway running with its
token (`COG_QWEN_TOKEN`) in this environment. Then bind this harness over it:

```sh
cd ../cog-workbench
pixi run suite -- bind --provider cog-system-one-adapter \
  --request ../cog-system-one-adapter/examples/bind-request.json
pixi run suite -- activate-composition --context cog-brief-router \
  --binding-id binding-system-one-qwen --revision 1
```

Workbench checks the referenced model binding against this package's
`model_requirement` (`text-generation`, `json-output`) and requires the
harness locality to equal the model's.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `model_binding` | — | the admitted OpenAI-compatible model binding |
| `locality` | `cloud` | must equal the model binding's locality |
| `answer_mode` | `probabilities` | `discrete` asks for one value per question (confidence 1) |
| `json_mode` | `false` | ask the gateway for JSON mode; the model binding must have `json-output` |
| `corrective_retries` | `1` | re-asks when the model's JSON does not fit the answer schema |

## How a turn runs

1. The turn, binding and model binding are checked: admitted, matching
   references, loopback gateway, approved gateway credential, features,
   locality, question types, and the System One task contract.
2. `system-one-adapter` builds its system prompt and per-request output schema
   and calls `GatewayProvider`, the only transport it is given. Each call must
   come back from the bound model with the gateway's provenance record. By
   default the prompt asks for JSON and nothing else is imposed: gateway JSON
   mode rejects a reply outright when a small model adds trailing text (local
   Qwen3-4B was seen adding a stray `}`). A single complete leading JSON object
   is passed on and counted in `salvaged_replies`; anything else goes to the
   adapter to refuse and correct.
3. The adapter decodes, re-asks on malformed output, normalises distributions
   and computes confidence with TypeSafe's published formulas.
4. The answers are checked against the questions and returned as
   `{"model", "answer_source": "llm-adapter", "answers", "usage"}`, with the
   gateway records in `provider_observations`.

## Third-party software

`system-one-adapter` and `typesafe-sdk` are MIT-licensed by TypeSafe AI and
installed from PyPI at pinned versions; they are dependencies, not vendored
code, and keep their own license terms.
