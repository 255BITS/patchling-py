# Relevance-filter benchmark

Compare `filter_relevant` with sending every file in a repository to `generate_diff`.

You supply:

- a repo path (scanned with the same `.gitignore` / `.gptignore` rules as the CLI)
- the task, in plain language
- a hand-labeled list of files that are actually relevant

The script reports precision, recall, and F1. Paths in the label list that the scan did not see are listed separately and are not counted as false negatives. Every other file is treated as not relevant, so an extra `yes` is a false positive.

It also reports tokens and estimated cost against sending the whole tree. Prices are USD per million tokens and default to placeholders (`--filter-input-price`, `--filter-output-price`, `--diff-input-price`, `--diff-output-price`). Replace them with the prices for the models you run. `smartapply` does not report usage, so its tokens are not in the total.

With a live key it then runs `generate_diff` and `smartapply` twice: once on every file, once on the files the filter kept. Both diffs are applied back onto the original tree (files the filter dropped stay untouched) and the script reports whether the diffs match, how similar they are, and which paths ended up with different contents. The JSON report stores each pipeline's diff text and applied file map. If one `generate_diff` or `smartapply` call fails, that error is recorded and the other pipeline is still written; the script does not stop.

## Run

Live calls are gated. Nothing is sent unless `LIVE_LLM=1` and `GPTDIFF_LLM_API_KEY` are set (same key and `GPTDIFF_LLM_BASE_URL` as the rest of the library).

```bash
export LIVE_LLM=1
export GPTDIFF_LLM_API_KEY='your-key'

python bench/relevance/run.py \
  --repo /path/to/repo \
  --task "Add structured logging to the API handlers" \
  --labels bench/relevance/labels.example.json \
  --filter-model xiaomi/mimo-v2.6-flash \
  --output report.json
```

Labels are either a JSON list, a JSON object with a `relevant` array, or a text file with one path per line (`#` comments are ignored). Paths are relative to the repo root.

`--skip-diff` scores the filter only. Cost then uses input-token estimates and does not include a model completion. `--decisions report.json` rescores a saved decision list; together with `--skip-diff` this does not call a model and does not require `LIVE_LLM`.

`--cache path.json` is the on-disk relevance cache (prompt version, model, output-token limit, the effort actually sent, task, file path, mode, preview note, and the body sent). Clear yes/no answers are stored. A cache read error is a miss and the model is called. The default filter model is `xiaomi/mimo-v2.6-flash`. A pallets/flask pass reported recall 1.0 and about 0.8x the token overhead of the next model tried; those figures are UNVERIFIED. The printed summary counts every file included because the answer was empty or malformed or the call failed after retries.
