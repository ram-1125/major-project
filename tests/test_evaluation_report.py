from __future__ import annotations

from analytics.evaluation_report import classification_metrics, render_markdown


def test_classification_metrics_use_requested_binary_formulas() -> None:
    result = classification_metrics(tp=30, tn=50, fp=10, fn=10)
    assert result["evaluated_windows"] == 100
    assert result["normal_samples"] == 60
    assert result["failure_risk_samples"] == 40
    assert result["accuracy"] == 0.8
    assert result["recall"] == 0.75
    assert result["specificity"] == 50 / 60
    assert result["balanced_accuracy"] == (0.75 + 50 / 60) / 2
    assert result["false_positive_rate"] == 10 / 60
    assert result["false_negative_rate"] == 0.25
    assert result["precision"] == 0.75
    assert result["f1_score"] == 0.75


def test_undefined_metrics_remain_null_and_ppt_table_is_truthful() -> None:
    metrics = classification_metrics(tp=0, tn=0, fp=0, fn=0)
    assert metrics["accuracy"] is None
    assert metrics["balanced_accuracy"] is None
    assert metrics["precision"] is None
    assert metrics["false_positive_rate"] is None
    report = {
        "status": "insufficient_labeled_evidence",
        "validation_run": None,
        "blocking_reasons": ["no_eligible_labelled_evaluation_windows"],
        "metrics": metrics,
        "confusion_matrix_counts": {"TP": 0, "TN": 0, "FP": 0, "FN": 0},
        "sample_counts": {
            "evaluated_windows": 0,
            "normal_samples": 0,
            "failure_risk_samples": 0,
        },
    }
    rendered = render_markdown(report)
    assert "| Overall Accuracy | Not currently calculable |" in rendered
    assert "| Average Prediction Confidence | Not currently measurable |" in rendered
    assert "0.00%" not in rendered
