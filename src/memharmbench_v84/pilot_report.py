"""Attack-only V8.4.3 formal aggregation and full-288 report artifacts."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .identity import DATASET_REVISION
from .judges.atomic import require_fingerprint
from .metrics import (CHANNELS, FORMAL_JUDGE_SOURCES, METRICS, compute_metrics,
                      episode_id, exposure_label, trigger_labels, terminal_failure,
                      valid_label)

# Compatibility entry points share the exact denominator implementation.
_pmir = exposure_label
_trigger = trigger_labels
_terminal_failure = terminal_failure


def aggregate_full(rows: Iterable[Mapping[str, Any]], expected_case_ids: Sequence[str],
                   repeats: int = 1, bootstrap_samples: int = 2000,
                   clean_case_ids: Sequence[str] | None = None) -> dict[str, Any]:
    rows, expected = list(rows), list(expected_case_ids)
    if not expected or len(set(expected)) != len(expected):
        raise ValueError("a nonempty list of distinct expected cases is required")
    if repeats != 1:
        raise ValueError("V8.4.3 freezes one episode per case; repeated episodes need a new statistical contract")
    if clean_case_ids:
        raise ValueError("V8.4.3 has no control arm")
    fingerprints = set()
    for row in rows:
        if row.get("case_id") not in expected or row.get("replicate_id") != 1 or row.get("condition") != "attack":
            raise ValueError("result is outside the frozen attack-only cohort")
        if row.get("channel") not in CHANNELS:
            raise ValueError("unrecognized channel")
        if row.get("dataset_revision") != DATASET_REVISION or row.get("judge_source") not in FORMAL_JUDGE_SOURCES:
            raise ValueError("only current V8.4.3 formal QuickRouter judgments enter a final report")
        fingerprints.add(require_fingerprint(row.get("evaluation_contract_fingerprint")))
    if len(fingerprints) > 1:
        raise ValueError("mixed evaluation contracts cannot be aggregated")
    overall = compute_metrics(rows)
    seen = {r["case_id"] for r in rows}
    missing = [{"case_id": cid, "condition": "attack", "replicate_id": 1} for cid in expected if cid not in seen]
    channels = {label: compute_metrics([r for r in rows if r["channel"] == channel])
                for channel, label in CHANNELS.items()}
    objectives = sorted({r.get("objective_id", "?") for r in rows}
                        | {f"{prefix}{index}" for prefix in ("u", "w", "x") for index in range(1, 5)})
    by_objective = {name: compute_metrics([r for r in rows if r.get("objective_id") == name])
                    for name in objectives}
    complete = not missing and not overall["unresolved"]
    return {
        "schema_version": "v843-formal-report-2", "dataset_revision": DATASET_REVISION,
        "evaluation_contract_fingerprint": next(iter(fingerprints), None),
        "status": "complete" if complete else "partial_or_unresolved",
        "final_metrics_available": complete, "cohort_cases": len(expected),
        "attack_cases": len(expected), "clean_cases": 0, "repeats": 1,
        "planned_episodes": len(expected), "formal_rows": len(rows), "missing_units": missing,
        "unresolved_units": len(overall["unresolved"]), "overall": overall,
        "attack": channels, "by_channel": channels, "by_objective": by_objective,
        "metric_denominator_policy": overall["metric_denominator_policy"],
        "technical_failure_units": [r["case_id"] for r in rows if terminal_failure(r)],
        "quality_counts": {
            "per_metric_null": overall["counts"]["per_metric_null"],
            "technical_failure": overall["counts"]["technical_failures"],
            "judge_insufficient": overall["counts"]["judge_insufficient"],
            "human_review": overall["counts"]["human_review"],
        },
        "limitations": [
            "One independent episode per case; Wilson 95% intervals are case-episode intervals.",
            "No clean or positive-control arm is included; causal attributable harm is not estimated.",
            "Unresolved judgments are reported and excluded from the common trigger denominator.",
            "A zero count does not establish zero population probability.",
        ],
    }


def aggregate_pilot(rows: Iterable[Mapping[str, Any]], expected_case_ids: Sequence[str],
                    repeats: int = 1, bootstrap_samples: int = 2000,
                    clean_case_ids: Sequence[str] | None = None) -> dict[str, Any]:
    """Compatibility wrapper; V8.4.3 has one formal attack-only aggregation."""
    return aggregate_full(rows, expected_case_ids, repeats, bootstrap_samples, clean_case_ids)


def _fmt(metric: Mapping[str, Any]) -> str:
    if metric["value"] is None:
        return "N/A"
    ci = metric["wilson_95_ci"]
    return (f"{100 * metric['value']:.2f}% ({metric['positive']}/{metric['denominator']}; "
            f"95% CI {100 * ci['lower']:.2f}–{100 * ci['upper']:.2f}%)")


def _json_write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _final_metric(row: Mapping[str, Any], name: str) -> int | None:
    if name == "PMIR":
        return exposure_label(row)
    return valid_label((row.get("trigger") or {}).get(name.lower()))


def _case_status_rows(rows: Sequence[Mapping[str, Any]], expected_case_ids: Sequence[str],
                      summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    indexed = {row["case_id"]: row for row in rows}
    quality = summary["overall"]["quality"]
    categories: dict[str, dict[str, set[str]]] = {}
    for name in METRICS:
        categories[name] = {
            "null": set(quality["per_metric_null"][name]["episode_ids"]),
            "technical_failure": set(quality["technical_failure"]["per_metric"][name]["episode_ids"]),
            "judge_insufficient": {item["episode_id"] for item in quality["judge_insufficient"]["entries"]
                                   if item["metric"] == name},
            "human_review": {item["episode_id"] for item in quality["human_review"]["entries"]
                             if item["metric"] == name},
        }
    output = []
    for case_id in expected_case_ids:
        row = indexed.get(case_id)
        if row is None:
            output.append({"episode_id": f"{case_id}#r1", "case_id": case_id, "present": False,
                           "state": "missing", "condition": "attack", "replicate_id": 1})
            continue
        eid = episode_id(row)
        trigger_applicable = ((row.get("exposure") or {}).get("final_pmir") == 1
                              or (row.get("exposure") or {}).get("trigger_status") in ("run", "technical_failure")
                              or bool(row.get("trigger")))
        item: dict[str, Any] = {
            "episode_id": eid, "case_id": case_id, "present": True,
            "state": row.get("state"), "condition": row.get("condition"),
            "replicate_id": row.get("replicate_id"), "channel": CHANNELS[row["channel"]],
            "objective_id": row.get("objective_id"), "trigger_eligible": trigger_labels(row) is not None,
            "technical_failure": terminal_failure(row), "failure": row.get("failure"),
        }
        for name in METRICS:
            applies = name == "PMIR" or trigger_applicable
            item[name] = _final_metric(row, name) if applies else None
            item[f"{name}_applicable"] = applies
            for category, ids in categories[name].items():
                item[f"{name}_{category}"] = eid in ids
        output.append(item)
    return output


def _scope_reports(summary: Mapping[str, Any]) -> list[tuple[str, str, Mapping[str, Any]]]:
    return ([("overall", "all", summary["overall"])]
            + [("channel", name, report) for name, report in summary["by_channel"].items()]
            + [("objective", name, report) for name, report in summary["by_objective"].items()])


def _metrics_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        "# V8.4.3 full-288 formal metrics", "",
        f"Status: {summary['status']}; {summary['cohort_cases']} attack cases; one episode per case.", "",
        "| Scope | PMIR | C-ASR | UTDR | ATAR |", "|---|---:|---:|---:|---:|",
    ]
    for kind, value, report in _scope_reports(summary):
        scope = "Overall" if kind == "overall" else f"{kind}: {value}"
        lines.append(f"| {scope} | " + " | ".join(_fmt(report["metrics"][name]) for name in METRICS) + " |")
    lines += ["", "## Reporting quality", "",
              "| Metric | Null | Technical failure | Judge insufficient | Human review |",
              "|---|---:|---:|---:|---:|"]
    quality = summary["overall"]["quality"]
    for name in METRICS:
        lines.append(
            f"| {name.replace('_', '-')} | {quality['per_metric_null'][name]['count']} | "
            f"{quality['technical_failure']['per_metric'][name]['count']} | "
            f"{quality['judge_insufficient']['per_metric'][name]} | "
            f"{quality['human_review']['per_metric'][name]} |"
        )
    lines += ["", summary["metric_denominator_policy"], "",
              f"Unresolved: {summary['unresolved_units']}; missing: {len(summary['missing_units'])}.", ""]
    return "\n".join(lines)


def _metrics_csv(summary: Mapping[str, Any]) -> str:
    buffer = io.StringIO(newline="")
    fields = ["scope", "scope_value", "metric", "positive", "denominator", "value",
              "wilson_95_ci_lower", "wilson_95_ci_upper", "null", "technical_failure",
              "judge_insufficient", "human_review"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for kind, value, report in _scope_reports(summary):
        quality = report["quality"]
        for name in METRICS:
            metric, ci = report["metrics"][name], report["metrics"][name]["wilson_95_ci"]
            writer.writerow({
                "scope": kind, "scope_value": value, "metric": name,
                "positive": metric["positive"], "denominator": metric["denominator"],
                "value": metric["value"],
                "wilson_95_ci_lower": ci["lower"] if ci else None,
                "wilson_95_ci_upper": ci["upper"] if ci else None,
                "null": quality["per_metric_null"][name]["count"],
                "technical_failure": quality["technical_failure"]["per_metric"][name]["count"],
                "judge_insufficient": quality["judge_insufficient"]["per_metric"][name],
                "human_review": quality["human_review"]["per_metric"][name],
            })
    return buffer.getvalue()


def write_full_report(root: Path, rows: Iterable[Mapping[str, Any]], expected_case_ids: Sequence[str],
                      repeats: int = 1, *, report_dir: Path | None = None) -> dict[str, Any]:
    """Write the five frozen full-288 review artifacts and return their summary."""
    rows = list(rows)
    expected_case_ids = list(expected_case_ids)
    summary = aggregate_full(rows, expected_case_ids, repeats)
    out = report_dir if report_dir is not None else root / "reports"
    out.mkdir(parents=True, exist_ok=True)
    statuses = _case_status_rows(rows, expected_case_ids, summary)
    status_document = {
        "schema_version": "v843-full288-case-status-1",
        "dataset_revision": summary["dataset_revision"],
        "evaluation_contract_fingerprint": summary["evaluation_contract_fingerprint"],
        "expected_cases": len(expected_case_ids), "rows": statuses,
    }
    _json_write(out / "full288_case_status.json", status_document)
    buffer = io.StringIO(newline="")
    fields = ["episode_id", "case_id", "present", "state", "channel", "objective_id",
              "PMIR", "C_ASR", "UTDR", "ATAR", "trigger_eligible", "technical_failure",
              *[f"{name}_{category}" for name in METRICS
                for category in ("applicable", "null", "technical_failure", "judge_insufficient", "human_review")]]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(statuses)
    (out / "full288_case_status.csv").write_text(buffer.getvalue(), encoding="utf-8-sig", newline="")
    (out / "full288_metrics.md").write_text(_metrics_markdown(summary), encoding="utf-8")
    (out / "full288_metrics.csv").write_text(_metrics_csv(summary), encoding="utf-8-sig", newline="")
    unresolved = {
        "schema_version": "v843-full288-unresolved-1",
        "dataset_revision": summary["dataset_revision"],
        "evaluation_contract_fingerprint": summary["evaluation_contract_fingerprint"],
        "missing_units": summary["missing_units"],
        "unresolved": summary["overall"]["unresolved"],
        "technical_failure": summary["overall"]["quality"]["technical_failure"],
        "judge_insufficient": summary["overall"]["quality"]["judge_insufficient"],
        "human_review": summary["overall"]["quality"]["human_review"],
    }
    _json_write(out / "full288_unresolved.json", unresolved)
    return summary


def write_pilot_report(root: Path, rows: Iterable[Mapping[str, Any]], expected_case_ids: Sequence[str],
                       repeats: int = 1) -> dict[str, Any]:
    """Compatibility wrapper that writes only the formal full-288 artifact names."""
    return write_full_report(root, rows, expected_case_ids, repeats)
