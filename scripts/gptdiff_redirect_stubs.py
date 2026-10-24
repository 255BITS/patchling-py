#!/usr/bin/env python3
"""Turn a built mkdocs site into redirect stubs for its new home.

    python scripts/gptdiff_redirect_stubs.py public https://patchling.app/py/

Used only by the Deploy Documentation workflow (gptdiff.255labs.xyz is GitHub
Pages, which has no server-side redirects). Every page becomes a stub with a
canonical link, a meta refresh and a script that keeps the query and #anchor.
404.html maps any other path: known pages (with or without trailing slash or
index.html) go to their match, everything else to the base URL. All other
files are removed, except CNAME. The mkdocs source is untouched, so the same
docs still build normally anywhere else (patchling.app/py/).
"""
from __future__ import annotations

import html
import json
import os
import shutil
import sys
from pathlib import Path

STUB = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Moved to {target_text}</title>
<link rel="canonical" href="{target}">
<meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url={target}">
<script>location.replace({target_js} + location.search + location.hash);</script>
</head>
<body><p>The patchling docs moved to <a href="{target}">{target_text}</a>.</p></body>
</html>
"""

NOT_FOUND = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Moved to {base_text}</title>
<meta name="robots" content="noindex">
<script>
(function () {{
  var base = {base_js};
  var known = {known_js};
  var p = location.pathname.replace(/index\\.html$/, "");
  if (p.charAt(p.length - 1) !== "/") p += "/";
  var rel = p.replace(/^\\/+/, "");
  var dest = known.indexOf(rel) >= 0 ? base + rel + location.search + location.hash : base;
  location.replace(dest);
}})();
</script>
<meta http-equiv="refresh" content="0; url={base}">
</head>
<body><p>The patchling docs moved to <a href="{base}">{base_text}</a>.</p></body>
</html>
"""


def page_paths(site: Path) -> list[str]:
    """Site-relative URL paths of the built pages: "" for /, "cli/" for /cli/."""
    out = []
    for f in sorted(site.rglob("*.html")):
        rel = f.relative_to(site).as_posix()
        if rel == "404.html":
            continue
        if rel == "index.html":
            out.append("")
        elif rel.endswith("/index.html"):
            out.append(rel[: -len("index.html")])
        else:
            out.append(rel)
    return sorted(out)


def js_json(value) -> str:
    """JSON for embedding in an inline <script>: escape < (and >, &) so a value
    can never close the script element or open a comment."""
    return (json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def stub(target: str) -> str:
    return STUB.format(target=html.escape(target, quote=True), target_text=html.escape(target),
                       target_js=js_json(target))


def build(site: Path, base: str) -> list[str]:
    if not base.endswith("/"):
        base += "/"
    paths = page_paths(site)
    if not paths:
        raise SystemExit(f"no pages under {site}")
    cname = (site / "CNAME").read_text() if (site / "CNAME").exists() else None
    for child in site.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    for rel in paths:
        dest = site / (rel + "index.html" if rel == "" or rel.endswith("/") else rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(stub(base + rel))
    (site / "404.html").write_text(NOT_FOUND.format(
        base=html.escape(base, quote=True), base_text=html.escape(base),
        base_js=js_json(base), known_js=js_json([p for p in paths if p]),
    ))
    if cname is not None:
        (site / "CNAME").write_text(cname)
    return paths


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[2].strip(), file=sys.stderr)
        return 2
    paths = build(Path(argv[1]), argv[2])
    print(f"wrote {len(paths)} redirect stubs + 404.html -> {argv[2]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
