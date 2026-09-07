"""Read-only, PPT-oriented classification evaluation for SmartOps.

This module never runs validation, creates labels, or writes to SQLite.  It
reports only a stored Phase 5B run whose evidence signature is still current.
Risk Evidence Index, deviation scores, Health Score, and alert evidence
confidence are not prediction probabilities and are never averaged here.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent.config import get_database_path
from analytics.validation import evidence_signature
from backend.database import read_only_database_connection


CONFIDENCE_UNAVAILABLE = "Average Prediction Confidence: Not currently measurable"
COUNT_METRICS = {
    "tp": "true_positive_window_count",
    "tn": "true_negative_window_count",
    "fp": "false_positive_window_count",
    "fn": "false_negative_window_count",
}


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def classification_metrics(tp: int, tn: int, fp: int, fn: int) -> dict[str, Any]:
    """Calculate binary metrics without substituting zero for undefined ratios."""
    total = tp + tn + fp + fn
    recall = _ratio(tp, tp + fn)
    specificity = _ratio(tn, tn + fp)
    precision = _ratio(tp, tp + fp)
    return {
        "true_positives": tp,
        "true_negatives": tn,
        "false_positives": fp,
        "false_negatives": fn,
        "evaluated_windows": total,
        "normal_samples": tn + fp,
        "failure_risk_samples": tp + fn,
        "predicted_normal_samples": tn + fn,
        "predicted_failure_risk_samples": tp + fp,
        "accuracy": _ratio(tp + tn, total),
        "balanced_accuracy": (
            (recall + specificity) / 2
            if recall is not None and specificity is not None
            else None
        ),
        "precision": precision,
        "recall": recall,
        "specificity": specificity,
        "false_positive_rate": _ratio(fp, fp + tn),
        "false_negative_rate": _ratio(fn, fn + tp),
        "f1_score": (
            2 * precision * recall / (precision + recall)
            if precision is not None
            and recall is not None
            and precision + recall > 0
            else None
        ),
    }


def _table_exists(connection: Any, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _count(connection: Any, table: str, where: str = "", values: tuple[Any, ...] = ()) -> int:
    if not _table_exists(connection, table):
        return 0
    return int(connection.execute(f"SELECT COUNT(*) FROM {table} {where}", values).fetchone()[0])


def _metric_rows(connection: Any, run_id: int) -> dict[str, dict[str, Any]]:
    return {
        row["metric_name"]: dict(row)
        for row in connection.execute(
            """SELECT * FROM validation_metric_results
            WHERE evaluation_run_id = ? AND scope_type = 'overall'
            AND scope_value = 'all'""",
            (run_id,),
        )
    }


def build_evaluation_report(database_path: Path | None = None) -> dict[str, Any]:
    """Inspect the newest stored validation run through a query-only connection."""
    path = (database_path or get_database_path()).resolve()
    with read_only_database_connection(path) as connection:
        schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        run_row = connection.execute(
            """SELECT * FROM validation_evaluation_runs
            ORDER BY finished_at_utc DESC, id DESC LIMIT 1"""
        ).fetchone() if _table_exists(connection, "validation_evaluation_runs") else None
        inventory = {
            "observation_periods": _count(connection, "validation_observation_periods"),
            "completed_observation_periods": _count(
                connection, "validation_observation_periods", "WHERE state = 'completed'"
            ),
            "incident_reports": _count(connection, "incident_reports"),
            "active_verified_incidents": _count(
                connection,
                "incident_reports",
                "WHERE status = 'active' AND verification_status IN ('user_reported','externally_verified')",
            ),
            "alert_feedback_labels": _count(connection, "alert_feedback", "WHERE status = 'active'"),
            "confirmed_alert_incident_links": _count(
                connection,
                "alert_incident_links",
                "WHERE match_type = 'confirmed_match' AND origin = 'manual' AND confirmed_by_user = 1",
            ),
            "published_validation_registry_records": _count(connection, "validation_registry"),
        }
        if run_row is None:
            return _unavailable_report(path, schema_version, inventory, "no_validation_run")

        run = dict(run_row)
        metrics = _metric_rows(connection, int(run["id"]))
        counts = {
            key: int(float(metrics.get(metric_name, {}).get("metric_value") or 0))
            for key, metric_name in COUNT_METRICS.items()
        }
        calculated = classification_metrics(
            counts["tp"], counts["tn"], counts["fp"], counts["fn"]
        )
        current_signature = evidence_signature(connection, run["device_id"])
        stale = current_signature != run["evidence_signature"]
        accuracy_row = metrics.get("accuracy", {})
        balanced_row = metrics.get("balanced_accuracy", {})
        publishable = (
            not stale
            and run["status"] == "evaluated"
            and accuracy_row.get("evaluation_state") == "evaluated"
            and balanced_row.get("evaluation_state") == "evaluated"
            and calculated["evaluated_windows"] > 0
        )
        invalid_reasons: list[str] = []
        if stale:
            invalid_reasons.append("stored_validation_run_predates_current_label_evidence")
        if run["status"] != "evaluated":
            invalid_reasons.append(str(run["status"]))
        if accuracy_row.get("evaluation_state") != "evaluated":
            invalid_reasons.extend(json.loads(accuracy_row.get("reason_codes_json") or "[]"))
        if balanced_row.get("evaluation_state") != "evaluated":
            invalid_reasons.extend(json.loads(balanced_row.get("reason_codes_json") or "[]"))
        if not calculated["evaluated_windows"]:
            invalid_reasons.append("no_eligible_labelled_evaluation_windows")

        published_metrics = {
            key: value if publishable else None
            for key, value in calculated.items()
            if key not in {
                "true_positives", "true_negatives", "false_positives", "false_negatives",
                "evaluated_windows", "normal_samples", "failure_risk_samples",
                "predicted_normal_samples", "predicted_failure_risk_samples",
            }
        }
        normal = calculated["normal_samples"]
        failure = calculated["failure_risk_samples"]
        imbalance = None
        if normal and failure:
            imbalance = max(normal, failure) / min(normal, failure)
        return {
            "status": "valid_labelled_evaluation" if publishable else "insufficient_labeled_evidence",
            "scientifically_publishable": publishable,
            "database_path": str(path),
            "schema_version": schema_version,
            "validation_run": {
                "id": run["id"],
                "started_at_utc": run["started_at_utc"],
                "finished_at_utc": run["finished_at_utc"],
                "status": run["status"],
                "algorithm_version": run["algorithm_version"],
                "configuration_version": run["configuration_version"],
                "matching_version": run["matching_version"],
                "stored_evidence_is_current": not stale,
            },
            "ground_truth_inventory": inventory,
            "evaluation_unit": "completed five-minute feature windows in eligible observation periods",
            "class_mapping": {"0": "Normal / No Failure Risk", "1": "Failure Risk"},
            "training_separation": (
                "Feature windows linked to baseline training are excluded from new validation runs."
            ),
            "confusion_matrix_counts": {
                "TP": counts["tp"], "TN": counts["tn"],
                "FP": counts["fp"], "FN": counts["fn"],
            },
            "sample_counts": {
                "evaluated_windows": calculated["evaluated_windows"],
                "normal_samples": calculated["normal_samples"],
                "failure_risk_samples": calculated["failure_risk_samples"],
            },
            "metrics": published_metrics,
            "average_prediction_confidence": None,
            "average_prediction_confidence_display": CONFIDENCE_UNAVAILABLE,
            "prediction_confidence_reason": (
                "SmartOps produces deterministic evidence indices and evidence-quality confidence, "
                "not a calibrated probability for Class 0 or Class 1."
            ),
            "class_imbalance_ratio": imbalance if publishable else None,
            "blocking_reasons": sorted(set(invalid_reasons)),
            "formulae": {
                "accuracy": "(TP + TN) / (TP + TN + FP + FN)",
                "recall_tpr": "TP / (TP + FN)",
                "specificity_tnr": "TN / (TN + FP)",
                "balanced_accuracy": "(TPR + TNR) / 2",
                "false_positive_rate": "FP / (FP + TN)",
                "false_negative_rate": "FN / (FN + TP)",
                "precision": "TP / (TP + FP)",
                "f1_score": "2 * (Precision * Recall) / (Precision + Recall)",
            },
        }


def _unavailable_report(
    path: Path, schema_version: int, inventory: dict[str, int], reason: str
) -> dict[str, Any]:
    return {
        "status": "insufficient_labeled_evidence",
        "scientifically_publishable": False,
        "database_path": str(path),
        "schema_version": schema_version,
        "validation_run": None,
        "ground_truth_inventory": inventory,
        "confusion_matrix_counts": {"TP": 0, "TN": 0, "FP": 0, "FN": 0},
        "sample_counts": {"evaluated_windows": 0, "normal_samples": 0, "failure_risk_samples": 0},
        "metrics": {
            name: None for name in (
                "accuracy", "balanced_accuracy", "precision", "recall", "specificity",
                "false_positive_rate", "false_negative_rate", "f1_score",
            )
        },
        "average_prediction_confidence": None,
        "average_prediction_confidence_display": CONFIDENCE_UNAVAILABLE,
        "prediction_confidence_reason": (
            "SmartOps does not emit a calibrated binary prediction probability."
        ),
        "class_imbalance_ratio": None,
        "blocking_reasons": [reason],
    }


def _percent(value: float | None) -> str:
    return "Not currently calculable" if value is None else f"{value * 100:.2f}%"


def render_markdown(report: dict[str, Any]) -> str:
    """Render a concise terminal/PPT report without inventing unavailable values."""
    metrics = report["metrics"]
    counts = report["confusion_matrix_counts"]
    samples = report["sample_counts"]
    lines = [
        "# SmartOps experimental evaluation",
        "",
        f"Status: **{report['status']}**",
        f"Validation run: {report.get('validation_run') or 'None'}",
        f"Blocking reasons: {', '.join(report.get('blocking_reasons', [])) or 'None'}",
        "",
        "## Confusion matrix",
        "",
        f"TP={counts['TP']}, TN={counts['TN']}, FP={counts['FP']}, FN={counts['FN']}",
        f"Evaluated windows={samples['evaluated_windows']}; Normal={samples['normal_samples']}; Failure Risk={samples['failure_risk_samples']}",
        "",
        "## Classification report",
        "",
        f"Precision: {_percent(metrics.get('precision'))}",
        f"Recall: {_percent(metrics.get('recall'))}",
        f"F1-score: {_percent(metrics.get('f1_score'))}",
        f"Specificity: {_percent(metrics.get('specificity'))}",
        "",
        "## PPT-ready table",
        "",
        "| Additional Metric | Result |",
        "|---|---:|",
        f"| Overall Accuracy | {_percent(metrics.get('accuracy'))} |",
        f"| Balanced Accuracy | {_percent(metrics.get('balanced_accuracy'))} |",
        "| Average Prediction Confidence | Not currently measurable |",
        f"| False Positive Rate | {_percent(metrics.get('false_positive_rate'))} |",
        f"| False Negative Rate | {_percent(metrics.get('false_negative_rate'))} |",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only SmartOps experimental evaluation report")
    parser.add_argument("--database", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = build_evaluation_report(args.database)
    print(json.dumps(report, indent=2) if args.json else render_markdown(report))


if __name__ == "__main__":
    main()
