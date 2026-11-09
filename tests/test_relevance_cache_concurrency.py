"""Disk-cache coordination uses local temporary files, never inference."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from patchling import relevance
from patchling.relevance import RelevanceCache


def record(raw):
    return {"decision": "yes", "raw": raw}


def test_claim_metadata_rejects_ambiguous_owner_values():
    owner = {"version": 1, "hostname": "host", "pid_scope": "linux:pid:[1]",
             "pid": 1, "ticket": 1, "token": "a" * 32}
    name = owner["token"] + ".json"
    assert relevance._valid_cache_claim(owner, name)
    for bad in [{"pid": True}, {"pid": 0}, {"pid": 1.5}, {"ticket": -1},
                {"ticket": 1 << 53}, {"hostname": ""}, {"pid_scope": ""},
                {"token": "b" * 32}, {"version": True}]:
        assert not relevance._valid_cache_claim({**owner, **bad}, name)


def test_non_posix_recovery_never_signals_an_owner(monkeypatch):
    probes = []
    owner = {"hostname": "host", "pid_scope": "win32"}
    with monkeypatch.context() as context:
        context.setattr(relevance.os, "name", "nt")
        context.setattr(relevance.os, "kill", lambda *args: probes.append(args))
        recovered = relevance._dead_cache_owner({**owner, "pid": 1234}, owner)
    assert recovered is False
    assert probes == []


def claim(path, **values):
    owner = {"version": 1, "hostname": socket.gethostname(),
             "pid_scope": relevance._cache_pid_scope(), "pid": os.getpid(),
             "token": "a" * 32, "ticket": 1, **values}
    directory = Path(str(path) + ".lock")
    directory.mkdir(exist_ok=True)
    target = directory / (owner["token"] + ".json")
    target.write_text(json.dumps(owner))
    return target


def test_stale_instances_merge_only_dirty_entries_and_preserve_newer_conflicts(tmp_path):
    path = tmp_path / "cache.json"
    a, b = RelevanceCache(path), RelevanceCache(path)
    a.put("shared", record("old")); a.flush()
    b.put("shared", record("new")); b.put("b", record("b")); b.flush()
    a.put("a", record("a")); a.flush()
    restored = RelevanceCache(path)
    assert restored.get("shared")["raw"] == "new"
    assert restored.get("a")["raw"] == "a"
    assert restored.get("b")["raw"] == "b"
    assert list(Path(str(path) + ".lock").iterdir()) == []


def test_concurrent_instances_reopen_with_all_records(tmp_path):
    path = tmp_path / "cache.json"
    caches = [RelevanceCache(path) for _ in range(6)]
    for index, cache in enumerate(caches):
        cache.put(str(index), record(str(index)))
    with ThreadPoolExecutor(max_workers=len(caches)) as workers:
        list(workers.map(lambda cache: cache.flush(), caches))
    payload = json.loads(path.read_text())
    assert payload["version"] == 1
    assert len(payload["entries"]) == len(caches)


def test_deleted_or_corrupt_file_does_not_resurrect_clean_snapshot(tmp_path):
    path = tmp_path / "cache.json"
    cache = RelevanceCache(path)
    cache.put("old", record("old")); cache.flush()
    path.unlink()
    cache.put("fresh", record("fresh")); cache.flush()
    assert RelevanceCache(path).get("old") is None
    path.write_text("{corrupt")
    cache.put("after-corruption", record("ok")); cache.flush()
    assert RelevanceCache(path).get("after-corruption")["raw"] == "ok"


def test_write_failure_logs_and_keeps_dirty_records_for_retry(tmp_path, monkeypatch, caplog):
    path = tmp_path / "cache.json"
    cache = RelevanceCache(path)
    cache.put("pending", record("pending"))
    replace = os.replace

    def fail_cache_replace(source, target):
        if os.fspath(target) == str(path):
            raise OSError("injected replacement failure")
        return replace(source, target)

    with monkeypatch.context() as context:
        context.setattr(os, "replace", fail_cache_replace)
        cache.flush()
    assert "injected replacement failure" in caplog.text
    assert list(Path(str(path) + ".lock").iterdir()) == []
    cache.flush()
    assert RelevanceCache(path).get("pending")["raw"] == "pending"


@pytest.mark.skipif(os.name != "posix", reason="automatic abandoned-owner recovery requires safe POSIX probes")
def test_abandoned_local_claim_is_recovered(tmp_path):
    path = tmp_path / "cache.json"
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=5)
    old = claim(path, pid=child.pid)
    cache = RelevanceCache(path)
    cache.put("a", record("a")); cache.flush()
    assert not old.exists()
    assert RelevanceCache(path).get("a")["raw"] == "a"
    assert old.parent.is_dir()


@pytest.mark.parametrize("kind", ["live", "foreign", "unknown"])
def test_never_steal_live_foreign_or_unknown_claims(tmp_path, monkeypatch, caplog, kind):
    path = tmp_path / "cache.json"
    values = {"hostname": socket.gethostname() + "-foreign"} if kind == "foreign" else {}
    blocker = claim(path, **values)
    if kind == "unknown":
        blocker.write_text("{malformed")
    monkeypatch.setattr(relevance, "_CACHE_LOCK_WAIT_SECONDS", 0.04)
    cache = RelevanceCache(path); cache.put("a", record("a"))
    began = time.monotonic(); cache.flush()
    assert time.monotonic() - began < 2
    assert "Timed out waiting" in caplog.text
    assert blocker.exists()
    blocker.unlink(); cache.flush()
    assert RelevanceCache(path).get("a")["raw"] == "a"


def test_independent_processes_overlap_without_losing_records(tmp_path):
    path = tmp_path / "cache.json"
    source = Path(__file__).resolve().parents[1]
    script = """
import os,sys,time
from patchling.relevance import RelevanceCache
path,key,barrier=sys.argv[1:]
cache=RelevanceCache(path);cache.put(key,{'decision':'yes','raw':key})
open(os.path.join(barrier,key),'w').close()
while not os.path.exists(os.path.join(barrier,'go')):time.sleep(.005)
cache.flush()
"""
    keys = ["p1", "p2", "p3", "p4"]
    processes = [subprocess.Popen([sys.executable, "-c", script, str(path), key, str(tmp_path)],
                                  cwd=source, env={**os.environ, "PYTHONPATH": str(source)},
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                 for key in keys]
    try:
        deadline = time.monotonic() + 10
        while not all((tmp_path / key).exists() for key in keys):
            assert time.monotonic() < deadline, "writer process did not become ready"
            time.sleep(0.01)
        (tmp_path / "go").touch()
        for process in processes:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, stdout + stderr
        restored = RelevanceCache(path)
        for key in keys:
            assert restored.get(key)["raw"] == key
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()
