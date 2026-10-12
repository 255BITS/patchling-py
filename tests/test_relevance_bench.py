"""Precision, cost, and diff-quality math for bench/relevance. No network."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


def _metrics():
    path = Path(__file__).resolve().parents[1] / "bench" / "relevance" / "metrics.py"
    spec = importlib.util.spec_from_file_location("relevance_bench_metrics", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_precision_recall_and_missing_labels():
    metrics = _metrics()
    decisions = [
        {"path": "a.py", "decision": "yes"},
        {"path": "b.py", "decision": "yes"},
        {"path": "c.py", "decision": "no"},
        {"path": "d.py", "decision": "no"},
    ]
    score = metrics.score_decisions(decisions, ["a.py", "c.py", "missing.py"])
    assert score["true_positives"] == ["a.py"]
    assert score["false_positives"] == ["b.py"]
    assert score["false_negatives"] == ["c.py"]
    assert score["missing_labels"] == ["missing.py"]
    assert score["precision"] == 0.5
    assert score["recall"] == 0.5
    assert score["f1"] == 0.5
    assert score["files_kept"] == 2


def test_empty_predictions_are_perfect_precision():
    metrics = _metrics()
    score = metrics.score_decisions(
        [{"path": "a.py", "decision": "no"}],
        [],
    )
    assert score["precision"] == 1.0
    assert score["recall"] == 1.0
    assert score["false_positives"] == []


def test_label_file_formats():
    metrics = _metrics()
    assert metrics.parse_label_list('["src/a.py", "src/b.py"]\n', filename="labels.json") == [
        "src/a.py",
        "src/b.py",
    ]
    parsed = metrics.parse_label_list(
        '{"relevant": ["src/a.py"]}\n',
        filename="rel.json",
    )
    assert parsed == ["src/a.py"]
    text = "# comment\n\nsrc/a.py\n./src/b.py\n"
    assert metrics.parse_label_list(text, filename="labels.txt") == ["src/a.py", "src/b.py"]


def test_cost_versus_sending_everything():
    metrics = _metrics()
    prices = {
        "filter_input": 0.10,
        "filter_output": 0.40,
        "diff_input": 1.25,
        "diff_output": 10.0,
    }
    # 1,000,000 filter input tokens → $0.10; 2,000,000 diff input tokens → $2.50.
    filtered = metrics.pipeline_cost(
        {"prompt_tokens": 1_000_000, "completion_tokens": 0},
        {"prompt_tokens": 2_000_000, "completion_tokens": 0},
        prices,
    )
    full = metrics.pipeline_cost(
        {"prompt_tokens": 0, "completion_tokens": 0},
        {"prompt_tokens": 8_000_000, "completion_tokens": 0},
        prices,
    )
    assert filtered["filter"] == 0.10
    assert filtered["diff"] == 2.50
    assert filtered["total"] == 2.60
    assert full["total"] == 10.0
    comparison = metrics.cost_comparison(full["total"], filtered["total"])
    assert comparison["savings"] == 7.4
    assert comparison["filtered_over_full"] == 0.26


def test_diff_quality_ignores_files_the_filter_dropped():
    metrics = _metrics()
    original = {"a.py": "a\n", "b.py": "b\n", "c.py": "c\n"}
    # Full pipeline rewrote a.py. Filtered pipeline rewrote a.py the same way
    # and never saw b.py or c.py.
    full_applied = {"a.py": "A\n", "b.py": "b\n", "c.py": "c\n"}
    filtered_subset_applied = {"a.py": "A\n"}
    merged = metrics.merge_applied(original, {"a.py": "a\n"}, filtered_subset_applied)
    quality = metrics.compare_outcomes(
        original,
        full_applied,
        merged,
        "--- a/a.py\n+++ b/a.py\n",
        "--- a/a.py\n+++ b/a.py\n",
    )
    assert quality["applied_exact_match"] is True
    assert quality["diff_exact_match"] is True
    assert quality["changed_paths_full"] == ["a.py"]
    assert quality["content_mismatches"] == []
    assert quality["diff_similarity"] == 1.0

    # A divergent apply shows up as a mismatch without pretending b.py was deleted.
    other = metrics.merge_applied(original, {"a.py": "a\n"}, {"a.py": "DIFFERENT\n"})
    diverged = metrics.compare_outcomes(original, full_applied, other, "one", "two")
    assert diverged["applied_exact_match"] is False
    assert diverged["content_mismatches"] == ["a.py"]
    assert "b.py" not in diverged["content_mismatches"]
    assert 0 < diverged["diff_similarity"] < 1


def test_benchmark_script_scores_saved_decisions_without_a_key(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "a.py").write_text("a = 1\n")
    (repo / "readme.md").write_text("# hi\n")
    labels = tmp_path / "labels.txt"
    labels.write_text("# relevant files\nsrc/a.py\n")
    decisions = tmp_path / "decisions.json"
    decisions.write_text(json.dumps({
        "model": "saved",
        "files": ["src/a.py"],
        "decisions": [
            {
                "path": "src/a.py",
                "decision": "yes",
                "reason": "yes",
                "raw": "yes",
                "tokens": 12,
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "cached": False,
                "skipped_reason": None,
                "error": None,
            },
            {
                "path": "readme.md",
                "decision": "no",
                "reason": "no",
                "raw": "no",
                "tokens": 9,
                "prompt_tokens": 8,
                "completion_tokens": 1,
                "cached": False,
                "skipped_reason": None,
                "error": None,
            },
        ],
    }))
    report_path = tmp_path / "report.json"
    script = Path(__file__).resolve().parents[1] / "bench" / "relevance" / "run.py"
    env = os.environ.copy()
    env.pop("LIVE_LLM", None)
    scored = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo",
            str(repo),
            "--task",
            "change a",
            "--labels",
            str(labels),
            "--decisions",
            str(decisions),
            "--skip-diff",
            "--output",
            str(report_path),
        ],
        cwd=str(script.parents[2]),
        env=env,
        capture_output=True,
        text=True,
    )
    assert scored.returncode == 0, scored.stderr + scored.stdout
    report = json.loads(report_path.read_text())
    assert report["score"]["precision"] == 1.0
    assert report["score"]["recall"] == 1.0
    assert report["files_kept"] == 1
    assert report["files_total"] == 2
    assert report["diff_quality"] is None
    assert "precision" in scored.stdout

    blocked = subprocess.run(
        [
            sys.executable,
            str(script),
            "--repo",
            str(repo),
            "--task",
            "change a",
            "--labels",
            str(labels),
        ],
        cwd=str(script.parents[2]),
        env=env,
        capture_output=True,
        text=True,
    )
    assert blocked.returncode == 2
    assert "LIVE_LLM=1" in blocked.stderr
