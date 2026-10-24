"""Run the patchling.app deploy-hook step against a fake curl."""
import os
import shutil
import subprocess
from pathlib import Path

import textwrap

import pytest

WF = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "patchling-app-deploy.yml"
HOOK = "https://api.cloudflare.com/client/v4/pages/webhooks/deploy_hooks/SECRET-HOOK-ID"


def run_block():
    """The single `run: |` block of the workflow, dedented (no PyYAML needed)."""
    lines = WF.read_text().splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip() == "run: |") + 1
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for l in lines[start:]:
        if l.strip() and len(l) - len(l.lstrip()) < indent:
            break
        body.append(l)
    return textwrap.dedent("\n".join(body)) + "\n"


def run_step(tmp_path, body, code="200", hook=HOOK):
    script = run_block()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (tmp_path / "body").write_text(body)
    curl = bindir / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        'while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; *) shift;; esac; done\n'
        f'cp "{tmp_path / "body"}" "$out"; printf %s "{code}"\n')
    curl.chmod(0o755)
    summary = tmp_path / "summary.md"
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", DEPLOY_HOOK_URL=hook,
               GITHUB_SHA="0123456789abcdef", GITHUB_STEP_SUMMARY=str(summary))
    p = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", script], cwd=tmp_path, env=env,
                       capture_output=True, text=True)
    out = p.stdout + p.stderr
    assert "SECRET-HOOK-ID" not in out
    return p.returncode, out, summary.read_text() if summary.exists() else ""


pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def test_valid_reply_queues(tmp_path):
    rc, out, summary = run_step(tmp_path, '{"success":true,"errors":[],"result":{"id":"a1b2c3d4-0000-4000-8000-123456789abc"}}')
    assert rc == 0, out
    assert "a1b2c3d4-0000-4000-8000-123456789abc" in out
    assert "not a finished deploy" in out and "not a finished deploy" in summary


@pytest.mark.parametrize("body", [
    '{"success":false,"errors":[{"code":8000000}],"result":null}',
    '{"success":true,"result":{}}',
    '{"success":"true","result":{"id":"a1b2c3d4-0000"}}',
    '<html>ok</html>',
    '',
])
def test_http_200_without_valid_json_fails(tmp_path, body):
    rc, out, _ = run_step(tmp_path, body)
    assert rc != 0
    assert "not a valid Cloudflare reply" in out


def test_non_200_fails(tmp_path):
    rc, out, _ = run_step(tmp_path, '{"success":true,"result":{"id":"a1b2c3d4-0000"}}', code="500")
    assert rc != 0 and "HTTP 500" in out


def test_unset_secret_skips(tmp_path):
    rc, out, _ = run_step(tmp_path, "", hook="")
    assert rc == 0 and "skipping" in out
