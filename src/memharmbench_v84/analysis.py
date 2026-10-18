"""Campaign analysis and report generation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .metrics import compute_metrics, stratified

STRATA_FIELDS = ("objective_id", "template_id", "context_id", "channel", "victim")


def _fmt(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def _ci(value: Mapping[str, Any] | None) -> str:
    if not value:
        return "N/A"
    return f"[{value['lower']:.3f}, {value['upper']:.3f}]"


def analyze_campaign(campaign_dir: Path) -> dict[str, Any]:
    campaign = json.loads(
        (campaign_dir / "campaign.json").read_text(encoding="utf-8")
    )
    results = campaign["results"]
    metrics = compute_metrics(results)
    strata = {field: stratified(results, field) for field in STRATA_FIELDS}
    analysis = {
        "schema_version": "8.4.3",
        **{key: campaign[key] for key in ("dataset_revision", "dataset_input_fingerprint") if key in campaign},
        "campaign_id": campaign.get("campaign_id"),
        "agent": campaign.get("agent"),
        "judge_source": campaign.get("judge_source", "unknown"),
        "case_count": len(results),
        "metrics": metrics,
        "strata": strata,
    }
    (campaign_dir / "analysis.json").write_text(
        json.dumps(analysis, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    report = render_report(analysis)
    (campaign_dir / "report.md").write_text(report, encoding="utf-8")
    return analysis


def render_report(analysis: Mapping[str, Any]) -> str:
    metrics = analysis["metrics"]
    counts = metrics["counts"]
    m = metrics["metrics"]
    lines = [
        f"# Campaign report: {analysis.get('campaign_id')}",
        "",
        f"- Agent: {analysis.get('agent')}",
        f"- Judge source: {analysis.get('judge_source')}",
        f"- Cases: {analysis.get('case_count')}",
        "",
    ]
    if analysis.get("dataset_revision"):
        lines += [
            f"- Dataset revision: {analysis['dataset_revision']}",
            f"- Dataset input fingerprint: `{analysis.get('dataset_input_fingerprint', 'unknown')}`",
            "",
        ]
    if analysis.get("judge_source") != "gpt-5-http":
        lines += [
            "## LABEL STATUS: PROVISIONAL ONLY",
            "",
            "This campaign ran without GPT-5 judge credentials. The PMIR gate is a",
            "transparent negation-aware rule-presence heuristic used ONLY to decide",
            "whether the trigger phase may run; trigger labels are all null. No final",
            "metric can be reported from this run. Run `python scripts/rejudge_campaign.py",
            f" {analysis.get('campaign_id')}` once JUDGE_API_KEY/OPENAI_API_KEY is set.",
            "",
        ]
    lines += [
        "## Workflow",
        "",
        f"- Reportable exposure chains: {counts['reportable_exposure']}",
        f"- PMIR success on first attempt: {metrics['workflow']['first_attempt_pmir_success']}",
        f"- PMIR success after the single pre-registered retry: {metrics['workflow']['retry_pmir_success']}",
        f"- Trigger skipped because final PMIR=0: {metrics['workflow']['trigger_skipped_pmir_zero']}",
        f"- Trigger runs actually completed: {metrics['workflow']['trigger_runs']}",
        f"- Exposure technical failures: {counts['exposure_technical_failures']}",
        f"- Trigger technical failures: {counts['trigger_technical_failures']}",
        f"- Judge technical failures: {counts['judge_technical_failures']}",
        f"- need_human_review entries: {len(metrics['need_human_review'])}",
        "",
        "## Core metrics (value [Wilson 95% CI], positive/denominator)",
        "",
    ]
    for name in ("PMIR", "C_ASR", "UTDR", "ATAR"):
        entry = m[name]
        lines.append(
            f"- {name}: {_fmt(entry['value'])} {_ci(entry['wilson_95_ci'])} "
            f"({entry['positive']}/{entry['denominator']})"
        )
    lines += ["", "## Stratified results (value, positive/denominator)", ""]
    for field, buckets in analysis["strata"].items():
        lines.append(f"### {field}")
        for key, metrics_per_bucket in buckets.items():
            entry = metrics_per_bucket["C_ASR"]
            lines.append(f"- {key}: C-ASR {_fmt(entry['value'])} ({entry['positive']}/{entry['denominator']})")
        lines.append("")
    lines += [
        "## Disclaimer",
        "",
        "This report describes a specific campaign. A 36-case pilot is an "
        "engineering qualification run and does NOT represent performance on "
        "the full 288-case benchmark.",
        "",
    ]
    if metrics["need_human_review"]:
        lines.append("## need_human_review")
        for item in metrics["need_human_review"]:
            lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def run_analyze(campaign_dir: Path) -> int:
    analyze_campaign(campaign_dir)
    print(f"analysis written to {campaign_dir / 'analysis.json'}")
    print(f"report written to {campaign_dir / 'report.md'}")
    return 0
