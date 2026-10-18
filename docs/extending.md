# Extending MemHarmBench

[Home](../README.md) · [Protocols and metrics](protocols.md)

## Understand one case first

```bash
memharmbench catalog --limit 6
memharmbench inspect i_u3_embedded_convention__commerce_orders
memharmbench demo
```

The authoring layer defines templates, objectives, context packs, and typed slots. Compiled cases resolve these definitions into concrete exposure and trigger inputs, business files, target values, and judging conditions. The released input manifest makes this relationship auditable without historical run artifacts.

## Add a context or template

Use `src/memharmbench_v84/catalog.py`, the `compiler/` package, `fixtures.py`, and `schema.py` to understand the authoring contracts. Keep a new dataset revision separate from the frozen public inputs.

- Write a legitimate task with an unambiguous normal completion condition.
- Specify the harmed party, protected decision, adversarial target, trigger condition, and persistent scope.
- Keep the attack carrier in the exposure surface. Do not copy its malicious instruction into the normal trigger task.
- Preserve access to the full legitimate business materials. A target appearing in a candidate list is not itself attack leakage.
- Define C-ASR for adoption of the target, UTDR for the remaining task requirements, and ATAR only for supported execution anomalies.
- Include source-authority metadata. An authorized-user message and an untrusted artifact are different threat conditions.

If a task requires a local operation, supply a bounded, deterministic operation contract and evidence validator. Keep these separate from the agent's self-reported success.

Validate the new revision and create a new input identity before running it. Do not update an existing campaign's inputs in place or merge observations from different fingerprints as if they shared one protocol.

## Add an agent adapter

Start with the interfaces under `src/memharmbench_v84/adapters/`. An adapter connects the actual agent runtime to the shared exposure/trigger controller. It should provide isolated workspace/session creation, native-memory capture and restoration, native session identities, and execution evidence.

Use a genuinely new native session for the trigger. Capture the configured and returned model identifiers, tool calls, termination state, and material needed to reconstruct whether the stage completed. A plain final-answer text file cannot substitute for native tool evidence when scoring ATAR.

Test that one case cannot read another case's memory, a trigger cannot reuse the exposure conversation, and copied distance branches cannot modify their source snapshots. Failed requests must not silently become successful empty responses. Unknown execution state must stop for reconciliation rather than replay into a possibly modified workspace.

## Preserve the metric contract

The implementation under `src/memharmbench_v84/judges/` defines atomic judging predicates and evidence validation; `metrics.py` owns denominator selection. Use these shared definitions rather than duplicating percentages in a new report.

Tests should include positive, negative, and unresolved judgments; missing native evidence; model mismatches; zero eligible triggers; and disagreement between attack adoption and formal task completion. Use hand-constructed synthetic fixtures, not private run outputs.

Run the offline checks before contributing:

```bash
python -m pip install -e ".[dev]"
memharmbench validate
python -m pytest
```

Describe any intentional change to threat model, input surface, exposure policy, retained state, or denominator in the contribution. Such changes are valuable extensions, but should carry their own protocol identity.
