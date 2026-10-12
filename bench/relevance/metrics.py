"""Pure scoring helpers for the relevance-filter benchmark.

No network and no patchling import: the runner supplies token counts and the
applied file maps.
"""

import json
import os


def norm_path(path: str) -> str:
    return os.path.normpath(str(path)).replace("\\", "/")


def parse_label_list(text: str, filename: str = "labels.txt") -> list:
    """Accept a JSON list, a JSON object with ``relevant``, or a text file."""
    if filename.endswith(".json"):
        data = json.loads(text)
        if isinstance(data, list):
            paths = data
        elif isinstance(data, dict) and isinstance(data.get("relevant"), list):
            paths = data["relevant"]
        else:
            raise ValueError("JSON labels must be a list or an object with a 'relevant' list")
        return [norm_path(path) for path in paths]

    paths = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        paths.append(norm_path(line))
    return paths


def _field(decision, name):
    if isinstance(decision, dict):
        return decision[name]
    return getattr(decision, name)


def score_decisions(decisions, relevant_paths) -> dict:
    """Precision and recall of yes-decisions against a hand-labeled relevant set.

    Paths in the label list that were not in the candidate set are reported as
    ``missing_labels`` and are left out of the recall denominator. Every other
    candidate the filter saw and said yes to, and that is not labeled, is a
    false positive. Precision is 1 when nothing was predicted relevant and
    there are no false positives; recall is 1 when no labeled file was present.
    """
    labeled = {norm_path(path) for path in relevant_paths}
    seen = []
    predicted = set()
    for decision in decisions:
        path = norm_path(_field(decision, "path"))
        seen.append(path)
        if _field(decision, "decision") == "yes":
            predicted.add(path)
    seen_set = set(seen)
    labeled_present = labeled & seen_set
    missing = sorted(labeled - seen_set)
    true_positive = sorted(predicted & labeled_present)
    false_positive = sorted(predicted - labeled)
    false_negative = sorted(labeled_present - predicted)
    precision = len(true_positive) / len(predicted) if predicted else 1.0
    recall_denom = len(labeled_present)
    recall = len(true_positive) / recall_denom if recall_denom else 1.0
    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": true_positive,
        "false_positives": false_positive,
        "false_negatives": false_negative,
        "missing_labels": missing,
        "predicted_relevant": sorted(predicted),
        "labeled_relevant": sorted(labeled),
        "files_seen": len(seen_set),
        "files_kept": len(predicted),
    }


def estimate_cost(prompt_tokens, completion_tokens, input_price_per_million, output_price_per_million) -> float:
    """USD cost from per-million token prices. Prices are caller-supplied estimates."""
    prompt = int(prompt_tokens or 0)
    completion = int(completion_tokens or 0)
    return (prompt / 1_000_000) * float(input_price_per_million) + (
        completion / 1_000_000
    ) * float(output_price_per_million)


def pipeline_cost(filter_usage, diff_usage, prices) -> dict:
    """Cost of a filter pass plus one generate_diff call.

    ``filter_usage`` / ``diff_usage`` are dicts with ``prompt_tokens`` and
    ``completion_tokens``. Diff-model prices and filter-model prices are
    separate because the filter is the cheap model.
    """
    filter_cost = estimate_cost(
        filter_usage.get("prompt_tokens", 0),
        filter_usage.get("completion_tokens", 0),
        prices["filter_input"],
        prices["filter_output"],
    )
    diff_cost = estimate_cost(
        diff_usage.get("prompt_tokens", 0),
        diff_usage.get("completion_tokens", 0),
        prices["diff_input"],
        prices["diff_output"],
    )
    return {"filter": filter_cost, "diff": diff_cost, "total": filter_cost + diff_cost}


def cost_comparison(full_total: float, filtered_total: float) -> dict:
    """Compare the filtered pipeline's cost with sending every file."""
    savings = full_total - filtered_total
    return {
        "full": full_total,
        "filtered": filtered_total,
        "savings": savings,
        "filtered_over_full": (filtered_total / full_total) if full_total else None,
    }


def compare_outcomes(original, full_applied, filtered_applied, full_diff, filtered_diff) -> dict:
    """Diff-quality comparison of the two pipelines applied to the same tree.

    ``full_applied`` and ``filtered_applied`` must both be full-tree maps (the
    runner merges a filtered smartapply result back onto ``original`` so files
    the filter dropped are not reported as deletions).
    """
    import difflib

    def changed(before, after):
        paths = set(before) | set(after)
        return {path for path in paths if before.get(path) != after.get(path)}

    changed_full = changed(original, full_applied)
    changed_filtered = changed(original, filtered_applied)
    union = changed_full | changed_filtered
    intersection = changed_full & changed_filtered
    keys = set(original) | set(full_applied) | set(filtered_applied)
    mismatches = sorted(path for path in keys if full_applied.get(path) != filtered_applied.get(path))
    full_text = full_diff or ""
    filtered_text = filtered_diff or ""
    return {
        "diff_exact_match": full_text.strip() == filtered_text.strip(),
        "applied_exact_match": full_applied == filtered_applied,
        "changed_paths_full": sorted(changed_full),
        "changed_paths_filtered": sorted(changed_filtered),
        "changed_path_jaccard": (len(intersection) / len(union)) if union else 1.0,
        "content_mismatches": mismatches,
        "diff_similarity": difflib.SequenceMatcher(a=full_text, b=filtered_text).ratio(),
    }


def merge_applied(original, subset_input, subset_applied) -> dict:
    """Project a smartapply result back onto the original full tree.

    Keys in ``subset_input`` that smartapply dropped are deletions. Keys it
    added are new files. Everything else stays as it was.
    """
    merged = dict(original)
    for path in subset_input:
        if path in subset_applied:
            merged[path] = subset_applied[path]
        else:
            merged.pop(path, None)
    for path, content in subset_applied.items():
        if path not in subset_input:
            merged[path] = content
    return merged
