"""MemHarmBench V8.4.3 schema.

All model definitions are self-contained. The V8 package never imports any
V3-V7 module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

SCHEMA_VERSION = "8.4.3"
METRICS = ("PMIR", "C_ASR", "UTDR", "ATAR")
CHANNELS = ("direct_instruction", "indirect_content")
VICTIMS = ("user", "workspace_owner", "external_stakeholder")


class V84SchemaError(ValueError):
    """Raised when a V8.4.3 object fails schema validation."""


# Kept as an import alias while the inherited authoring/compiler modules are
# migrated. Runtime data is still validated against the V8.4.3 fields below.
V7SchemaError = V84SchemaError


@dataclass(frozen=True)
class TypedSlots:
    trigger: str
    target_entity: str
    effect: str
    value: str
    scope: str

    def as_dict(self) -> dict[str, str]:
        return {
            "trigger": self.trigger,
            "target_entity": self.target_entity,
            "effect": self.effect,
            "value": self.value,
            "scope": self.scope,
        }


@dataclass(frozen=True)
class AttackTemplate:
    template_id: str
    victim: str
    objective_id: str
    objective_name: str
    channel: str
    delivery_role: str
    mechanism: str
    slots: TypedSlots
    persistent_rule: str
    judge_rubric_id: str
    attack_surface: str
    payload_template: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "victim": self.victim,
            "objective_id": self.objective_id,
            "objective_name": self.objective_name,
            "channel": self.channel,
            "delivery_role": self.delivery_role,
            "mechanism": self.mechanism,
            "slots": self.slots.as_dict(),
            "persistent_rule": self.persistent_rule,
            "judge_rubric_id": self.judge_rubric_id,
            "attack_surface": self.attack_surface,
            "payload_template": self.payload_template,
        }


@dataclass(frozen=True)
class Person:
    name: str
    email: str
    role: str
    authority: str

    def as_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "authority": self.authority,
        }


@dataclass(frozen=True)
class ContextPack:
    context_id: str
    name: str
    format: str
    org: str
    domain: str
    people: tuple[Person, ...]
    record_spec: Mapping[str, Any]
    business_facts: tuple[str, ...]
    history_lines: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "context_id": self.context_id,
            "name": self.name,
            "format": self.format,
            "org": self.org,
            "domain": self.domain,
            "people": [person.as_dict() for person in self.people],
            "record_spec": dict(self.record_spec),
            "business_facts": list(self.business_facts),
            "history_lines": list(self.history_lines),
        }


@dataclass(frozen=True)
class ObjectiveContextBinding:
    binding_id: str
    objective_id: str
    context_id: str
    exposure_reference: str
    attacker_role: str
    business_background: str
    trigger_task: str
    target_memory_rule: str
    protected_entity: str
    harm_outcome: str
    c_asr_condition: str
    utdr_condition: str
    atar_condition: str
    entities: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "objective_id": self.objective_id,
            "context_id": self.context_id,
            "exposure_reference": self.exposure_reference,
            "attacker_role": self.attacker_role,
            "business_background": self.business_background,
            "trigger_task": self.trigger_task,
            "target_memory_rule": self.target_memory_rule,
            "protected_entity": self.protected_entity,
            "harm_outcome": self.harm_outcome,
            "c_asr_condition": self.c_asr_condition,
            "utdr_condition": self.utdr_condition,
            "atar_condition": self.atar_condition,
            "entities": dict(self.entities),
        }


@dataclass(frozen=True)
class ResolvedCase:
    case_id: str
    template_id: str
    objective_id: str
    context_id: str
    channel: str
    victim: str
    seed: int
    provenance: Mapping[str, Any]
    binding: ObjectiveContextBinding
    exposure_input: str
    carrier_artifact: str
    trigger_input: str
    trigger_artifact: str
    payload_text: str
    payload_value: str
    target_memory_rule: str
    protected_entity: str
    harm_outcome: str
    entities: Mapping[str, Any]
    dataset_revision: str = ""
    prohibited_value: str = ""
    permission_condition: str = ""
    source_authority: str = ""
    judge_contract_sha256: str = ""
    evaluation_contract_fingerprint: str = ""
    evidence_dependencies: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Populate V8.4.3 identity fields from provenance for legacy loaders.

        The V8.4.3 JSON records carry these fields both at top level and in
        provenance. This fallback keeps inherited constructors source-compatible
        while still exposing the strict values to runtime/Judge code.
        """
        provenance = self.provenance
        fallbacks = {
            "dataset_revision": provenance.get("dataset_revision", ""),
            "permission_condition": provenance.get("permission_condition", ""),
            "source_authority": provenance.get("source_authority", ""),
            "judge_contract_sha256": provenance.get("judge_contract_sha256", ""),
            "evaluation_contract_fingerprint": provenance.get(
                "evaluation_contract_fingerprint", ""
            ),
        }
        for name, value in fallbacks.items():
            if not getattr(self, name) and value is not None:
                object.__setattr__(self, name, str(value))
        if not self.evidence_dependencies and isinstance(
            provenance.get("evidence_dependencies"), Mapping
        ):
            object.__setattr__(
                self,
                "evidence_dependencies",
                dict(provenance["evidence_dependencies"]),
            )
        if self.dataset_revision == "8.4.3":
            if not self.prohibited_value:
                raise ValueError("V8.4.3 resolved cases require prohibited_value")
            if self.binding.target_memory_rule != self.target_memory_rule:
                raise ValueError("V8.4.3 binding.target_memory_rule is not instantiated")
            if self.prohibited_value not in self.binding.c_asr_condition:
                raise ValueError("V8.4.3 binding.c_asr_condition omits prohibited_value")

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "template_id": self.template_id,
            "objective_id": self.objective_id,
            "context_id": self.context_id,
            "channel": self.channel,
            "victim": self.victim,
            "seed": self.seed,
            "provenance": dict(self.provenance),
            "binding": self.binding.as_dict(),
            "exposure_input": self.exposure_input,
            "carrier_artifact": self.carrier_artifact,
            "trigger_input": self.trigger_input,
            "trigger_artifact": self.trigger_artifact,
            "payload_text": self.payload_text,
            "payload_value": self.payload_value,
            "target_memory_rule": self.target_memory_rule,
            "protected_entity": self.protected_entity,
            "harm_outcome": self.harm_outcome,
            "entities": dict(self.entities),
            "dataset_revision": self.dataset_revision,
            "prohibited_value": self.prohibited_value,
            "permission_condition": self.permission_condition,
            "source_authority": self.source_authority,
            "judge_contract_sha256": self.judge_contract_sha256,
            "evaluation_contract_fingerprint": self.evaluation_contract_fingerprint,
            "evidence_dependencies": dict(self.evidence_dependencies),
        }


@dataclass(frozen=True)
class MetricResult:
    metric: str
    label: int | None
    confidence: float | None
    evidence: tuple[str, ...]
    reason: str
    need_human_review: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "label": self.label,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "reason": self.reason,
            "need_human_review": self.need_human_review,
        }


@dataclass(frozen=True)
class JudgeOutput:
    results: Mapping[str, MetricResult]

    def as_dict(self) -> dict[str, Any]:
        return {name: result.as_dict() for name, result in self.results.items()}
