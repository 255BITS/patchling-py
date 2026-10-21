#!/usr/bin/env python3
"""Benchmark the relevance filter against a hand-labeled relevant-file list.

Reports precision/recall, token use and estimated cost versus sending every
file, then runs generate_diff + smartapply both ways and compares the diffs.

Live model calls require LIVE_LLM=1 and GPTDIFF_LLM_API_KEY. Scoring a
previously saved decisions file with --skip-diff does not.

    python bench/relevance/run.py \\
        --repo /path/to/repo \\
        --task "Add structured logging to the API handlers" \\
        --labels bench/relevance/labels.example.json \\
        --output report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Allow `python bench/relevance/run.py` to import the sibling metrics module.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import metrics  # noqa: E402


# Placeholder USD-per-million prices. Override them with the flags below;
# they are not a quote from any provider.
DEFAULT_FILTER_INPUT_PRICE = 0.10
DEFAULT_FILTER_OUTPUT_PRICE = 0.40
DEFAULT_DIFF_INPUT_PRICE = 1.25
DEFAULT_DIFF_OUTPUT_PRICE = 10.0


def load_repo_files(repo: str) -> dict:
    """Scan ``repo`` with the library's gitignore rules.

    ``load_project_files`` matches ignore patterns against the process working
    directory, so the scan runs with that directory set to the repo and restores
    it afterward.
    """
    from patchling import load_project_files

    repo = os.path.abspath(repo)
    previous = os.getcwd()
    try:
        os.chdir(repo)
        pairs = load_project_files(repo, repo)
    finally:
        os.chdir(previous)
    files = {}
    for path, content in pairs:
        rel = os.path.relpath(path, repo).replace(os.sep, "/")
        files[rel] = content
    return files


def context_tokens(files: dict, task: str) -> int:
    from patchling import build_environment
    from patchling.relevance import count_tokens

    return count_tokens(build_environment(files)) + count_tokens(task)


def generate_diff_with_usage(files, task, model, api_key, base_url):
    """Same prompt as generate_diff, plus the token usage that function discards."""
    from patchling.core import build_environment, call_llm_for_diff

    environment = build_environment(files)
    system_prompt = 'Output a full unified git diff into a "```diff" block.'
    _full, diff, prompt_tokens, completion_tokens, total_tokens = call_llm_for_diff(
        system_prompt,
        task,
        environment,
        model,
        temperature=1.0,
        max_tokens=32000,
        api_key=api_key,
        base_url=base_url,
    )
    usage = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    return diff, usage


def run_pipeline(files, task, diff_model, apply_model, api_key, base_url):
    """Run generate_diff and smartapply.

    A failure in either step is recorded on the returned dict. The other
    pipeline, and this report, keep going.
    """
    from patchling import smartapply

    empty_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    try:
        diff, usage = generate_diff_with_usage(files, task, diff_model, api_key, base_url)
    except Exception as exc:
        return {
            "diff": None,
            "applied": None,
            "usage": empty_usage,
            "error": f"{type(exc).__name__}: {exc}",
        }
    try:
        applied = smartapply(diff, dict(files), model=apply_model, api_key=api_key, base_url=base_url)
    except Exception as exc:
        return {
            "diff": diff,
            "applied": None,
            "usage": usage,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {"diff": diff, "applied": applied, "usage": usage, "error": None}


def _as_pipeline(value):
    """Accept the dict from ``run_pipeline`` or a legacy ``(diff, usage, applied)`` tuple."""
    if value is None:
        return None
    if isinstance(value, dict):
        usage = value.get("usage") or {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        return {
            "diff": value.get("diff"),
            "applied": value.get("applied"),
            "usage": usage,
            "error": value.get("error"),
        }
    diff, usage, applied = value
    return {"diff": diff, "applied": applied, "usage": usage, "error": None}


def load_saved_decisions(path: str) -> list:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("decisions"), list):
        return payload["decisions"]
    raise ValueError(f"{path} is not a relevance-decisions JSON file")


def filter_usage_from_decisions(decisions) -> dict:
    prompt = 0
    completion = 0
    for decision in decisions:
        cached = bool(decision.get("cached", False)) if isinstance(decision, dict) else bool(decision.cached)
        if cached:
            continue
        if isinstance(decision, dict):
            prompt += int(decision.get("prompt_tokens") or 0)
            completion += int(decision.get("completion_tokens") or 0)
        else:
            prompt += int(decision.prompt_tokens or 0)
            completion += int(decision.completion_tokens or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def _failed_open_count(decisions) -> int:
    count = 0
    for decision in decisions:
        if isinstance(decision, dict):
            if decision.get("failed_open"):
                count += 1
        elif getattr(decision, "failed_open", False):
            count += 1
    return count


def yes_files(files: dict, decisions) -> dict:
    kept = {}
    for decision in decisions:
        path = decision["path"] if isinstance(decision, dict) else decision.path
        decision_value = decision["decision"] if isinstance(decision, dict) else decision.decision
        if decision_value == "yes" and path in files:
            kept[path] = files[path]
    return kept


def build_report(
    *,
    repo,
    task,
    decisions,
    relevant,
    files,
    prices,
    filter_model,
    diff_full=None,
    diff_filtered=None,
):
    """Assemble precision/recall, token, and cost figures.

    ``diff_full`` / ``diff_filtered`` are optional pipeline dicts (or legacy
    ``(diff, usage, applied)`` tuples). Applied maps are merged onto ``files``
    before the quality comparison. A pipeline that raised is stored with
    ``error`` set; its diff and applied files are kept when that step succeeded.
    """
    score = metrics.score_decisions(decisions, relevant)
    kept = yes_files(files, decisions)
    full_context = context_tokens(files, task)
    filtered_context = context_tokens(kept, task)
    filter_usage = filter_usage_from_decisions(decisions)

    # Input-only estimate used when the diff pipelines have not been run.
    # Output tokens are unknown until generate_diff actually returns.
    estimated_full_diff = {"prompt_tokens": full_context, "completion_tokens": 0}
    estimated_filtered_diff = {"prompt_tokens": filtered_context, "completion_tokens": 0}
    full_diff_usage = estimated_full_diff
    filtered_diff_usage = estimated_filtered_diff
    output_tokens_included = False
    quality = None

    full_pipeline = _as_pipeline(diff_full)
    filtered_pipeline = _as_pipeline(diff_filtered)
    if full_pipeline is not None and filtered_pipeline is not None:
        full_diff_usage = full_pipeline["usage"]
        filtered_diff_usage = filtered_pipeline["usage"]
        both_ok = full_pipeline["error"] is None and filtered_pipeline["error"] is None
        output_tokens_included = both_ok
        if (
            both_ok
            and full_pipeline["applied"] is not None
            and filtered_pipeline["applied"] is not None
        ):
            merged_filtered = metrics.merge_applied(files, kept, filtered_pipeline["applied"])
            merged_full = metrics.merge_applied(files, files, full_pipeline["applied"])
            quality = metrics.compare_outcomes(
                files,
                merged_full,
                merged_filtered,
                full_pipeline["diff"],
                filtered_pipeline["diff"],
            )

    full_cost = metrics.pipeline_cost({"prompt_tokens": 0, "completion_tokens": 0}, full_diff_usage, prices)
    filtered_cost = metrics.pipeline_cost(filter_usage, filtered_diff_usage, prices)
    comparison = metrics.cost_comparison(full_cost["total"], filtered_cost["total"])

    return {
        "repo": repo,
        "task": task,
        "filter_model": filter_model,
        "files_total": len(files),
        "files_kept": len(kept),
        "score": score,
        "tokens": {
            "filter": filter_usage,
            "full_context_estimate": full_context,
            "filtered_context_estimate": filtered_context,
            "context_tokens_avoided": full_context - filtered_context,
            "generate_diff_full": full_diff_usage,
            "generate_diff_filtered": filtered_diff_usage,
            "output_tokens_included": output_tokens_included,
            "note": (
                "smartapply does not report token usage, so it is omitted. "
                "Context estimates count the environment string plus the task "
                "and are a lower bound on the generate_diff prompt."
            ),
        },
        "cost": {
            "prices_usd_per_million": prices,
            "full_pipeline": full_cost,
            "filtered_pipeline": filtered_cost,
            "comparison": comparison,
            "note": "Prices are the estimates passed on the command line, not a provider invoice.",
        },
        "failed_open": _failed_open_count(decisions),
        "diff_quality": quality,
        "pipelines": {
            "full": full_pipeline,
            "filtered": filtered_pipeline,
        },
        "decisions": [
            decision if isinstance(decision, dict) else decision.to_dict() for decision in decisions
        ],
    }


def format_report(report: dict) -> str:
    score = report["score"]
    tokens = report["tokens"]
    cost = report["cost"]
    lines = [
        "Relevance benchmark",
        f"  repo: {report['repo']}",
        f"  task: {report['task']}",
        f"  filter model: {report['filter_model']}",
        (
            f"  files: {report['files_kept']} kept / {report['files_total']} seen"
            + (f", {report['failed_open']} failed open" if report.get("failed_open") else "")
        ),
        (
            f"  precision: {score['precision']:.3f}  "
            f"recall: {score['recall']:.3f}  f1: {score['f1']:.3f}"
        ),
        (
            f"  tokens: filter {tokens['filter']['total_tokens']}, "
            f"full context {tokens['full_context_estimate']}, "
            f"filtered context {tokens['filtered_context_estimate']}, "
            f"avoided {tokens['context_tokens_avoided']}"
        ),
        (
            f"  est. cost: full ${cost['comparison']['full']:.6f}, "
            f"filtered ${cost['comparison']['filtered']:.6f}, "
            f"savings ${cost['comparison']['savings']:.6f}"
        ),
    ]
    if score["false_positives"]:
        lines.append("  false positives: " + ", ".join(score["false_positives"]))
    if score["false_negatives"]:
        lines.append("  false negatives: " + ", ".join(score["false_negatives"]))
    if score["missing_labels"]:
        lines.append("  labels missing from the repo scan: " + ", ".join(score["missing_labels"]))
    pipelines = report.get("pipelines") or {}
    pipeline_errors = []
    for label in ("full", "filtered"):
        pipeline = pipelines.get(label)
        if pipeline and pipeline.get("error"):
            pipeline_errors.append(f"  {label} pipeline: {pipeline['error']}")
    lines.extend(pipeline_errors)
    quality = report.get("diff_quality")
    if quality:
        lines.append(
            f"  diff similarity: {quality['diff_similarity']:.3f}  "
            f"exact diff: {quality['diff_exact_match']}  "
            f"exact apply: {quality['applied_exact_match']}  "
            f"changed-path jaccard: {quality['changed_path_jaccard']:.3f}"
        )
        if quality["content_mismatches"]:
            lines.append("  apply mismatches: " + ", ".join(quality["content_mismatches"]))
    elif pipeline_errors:
        lines.append("  diff comparison: unavailable because a pipeline failed")
    elif report["tokens"]["output_tokens_included"] is False:
        lines.append("  diff comparison: skipped (cost above uses input-token estimates only)")
    return "\n".join(lines)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark patchling's relevance filter.")
    parser.add_argument("--repo", required=True, help="Repository directory to scan (respects .gitignore).")
    parser.add_argument("--task", required=True, help="Natural-language task the filter and the diff share.")
    parser.add_argument("--labels", required=True, help="Hand-labeled relevant files: JSON or one path per line.")
    parser.add_argument("--filter-model", default=None, help="Cheap model for filter_relevant.")
    parser.add_argument("--diff-model", default=None, help="Model for generate_diff. Defaults to GPTDIFF_MODEL.")
    parser.add_argument("--apply-model", default=None, help="Model for smartapply. Defaults to the diff model.")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--cache", default=None, help="On-disk relevance cache path.")
    parser.add_argument(
        "--decisions",
        default=None,
        help="Score this saved decisions JSON instead of calling the filter.",
    )
    parser.add_argument(
        "--skip-diff",
        action="store_true",
        help="Do not run generate_diff + smartapply. Cost uses input-token estimates.",
    )
    parser.add_argument("--output", default=None, help="Write the JSON report to this path.")
    parser.add_argument("--filter-input-price", type=float, default=DEFAULT_FILTER_INPUT_PRICE)
    parser.add_argument("--filter-output-price", type=float, default=DEFAULT_FILTER_OUTPUT_PRICE)
    parser.add_argument("--diff-input-price", type=float, default=DEFAULT_DIFF_INPUT_PRICE)
    parser.add_argument("--diff-output-price", type=float, default=DEFAULT_DIFF_OUTPUT_PRICE)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    needs_live_filter = args.decisions is None
    needs_live_diff = not args.skip_diff
    if (needs_live_filter or needs_live_diff) and os.environ.get("LIVE_LLM") != "1":
        print(
            "Set LIVE_LLM=1 to run the relevance benchmark against a live model. "
            "Scoring a saved decisions file offline is allowed with --decisions --skip-diff.",
            file=sys.stderr,
        )
        return 2
    if (needs_live_filter or needs_live_diff) and not os.getenv("GPTDIFF_LLM_API_KEY"):
        print("GPTDIFF_LLM_API_KEY is not set.", file=sys.stderr)
        return 2

    with open(args.labels, "r", encoding="utf-8") as handle:
        relevant = metrics.parse_label_list(handle.read(), filename=args.labels)

    files = load_repo_files(args.repo)
    api_key = os.getenv("GPTDIFF_LLM_API_KEY")
    base_url = os.getenv("GPTDIFF_LLM_BASE_URL", "https://nano-gpt.com/api/v1/")

    if args.decisions:
        decisions = load_saved_decisions(args.decisions)
        filter_model = "saved-decisions"
    else:
        from patchling import filter_relevant

        result = filter_relevant(
            files,
            args.task,
            model=args.filter_model,
            concurrency=args.concurrency,
            cache=args.cache,
            api_key=api_key,
            base_url=base_url,
        )
        decisions = result.decisions
        filter_model = result.model
        print(
            f"Filter kept {len(result.files)}/{len(files)} files "
            f"({result.billed_tokens} billed tokens, {len(result.failed_open)} failed open)."
        )

    diff_full = diff_filtered = None
    if not args.skip_diff:
        diff_model = args.diff_model or os.getenv("GPTDIFF_MODEL", "xiaomi/mimo-v2.6-pro")
        apply_model = args.apply_model or os.getenv("GPTDIFF_SMARTAPPLY_MODEL") or diff_model
        kept = yes_files(files, decisions)
        print(f"Running generate_diff + smartapply on all {len(files)} files...")
        diff_full = run_pipeline(files, args.task, diff_model, apply_model, api_key, base_url)
        if diff_full.get("error"):
            print(f"Full pipeline failed: {diff_full['error']}", file=sys.stderr)
        print(f"Running generate_diff + smartapply on {len(kept)} relevant files...")
        diff_filtered = run_pipeline(kept, args.task, diff_model, apply_model, api_key, base_url)
        if diff_filtered.get("error"):
            print(f"Filtered pipeline failed: {diff_filtered['error']}", file=sys.stderr)

    prices = {
        "filter_input": args.filter_input_price,
        "filter_output": args.filter_output_price,
        "diff_input": args.diff_input_price,
        "diff_output": args.diff_output_price,
    }
    report = build_report(
        repo=os.path.abspath(args.repo),
        task=args.task,
        decisions=decisions,
        relevant=relevant,
        files=files,
        prices=prices,
        filter_model=filter_model,
        diff_full=diff_full,
        diff_filtered=diff_filtered,
    )
    text = format_report(report)
    print(text)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
        print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
