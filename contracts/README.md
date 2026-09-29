# Vendored contracts

## satisfier-binding.schema.json

The OpenTeams manifest profile's draft `openteams/satisfier-binding [0.1-draft]`
schema, byte-identical to the copy vendored by the other suite providers
(cog-openrouter, cog-qwen, cog-turn-harness, cog-claude, cog-chatgpt).

SHA-256: `21315aa24a57ccdf02b8c5fba2b8517fd58876d63c6b04109483b06df9950130`

## src/system_one_contract.py

`openteams/system-one-turn [0.1-draft]`: the System One turn task and result.
Canonical source: cog-smith `templates/decision-cog/src/system_one_contract.py`
(decision-Cog machinery). Vendored byte-identically; fix it in Smith and copy.

SHA-256: `151d5e7219eef7b9a989ef4c9eb7feeeca6a9b991eb568c4fe6f1c86af3321d5`

Updates must copy the agreed file deliberately, update the digest here, and
rerun this package's tests and Smith's decision-Cog tests.
