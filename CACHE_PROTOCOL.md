# Relevance cache write coordination

The committed cache remains UTF-8 JSON in the existing shape:

```json
{"version": 1, "entries": {"cache-key": {"decision": "yes"}}}
```

No cache-key, prompt-version, or record-format migration is required. Each
instance tracks the records it has changed. A flush rereads the latest file,
merges **only those dirty records**, and atomically replaces the JSON file.
For conflicting keys, the last serialized flush wins. An old clean snapshot
never overwrites a newer record or resurrects records after the file is cleared.
A failed flush retains unsaved records so the same instance can retry.

## Shared Node/Python protocol

Updated Node and Python writers coordinate through `<absolute-cache-path>.lock/`
on a local filesystem. It is a persistent directory, usually empty when idle.
It must not be deleted while writers are active. Memory-only/browser caches do
not access the filesystem or acquire claims.

Each contender uses a new random 32-character lowercase hexadecimal token and
atomically publishes `<token>.json` with these fields:

```json
{"version": 1, "hostname": "host", "pid_scope": "linux:pid:[123]", "pid": 1234, "token": "0123456789abcdef0123456789abcdef", "ticket": 0}
```

`ticket: 0` announces that the contender is choosing. It then publishes one more
than the greatest visible ticket and waits until no contender is still choosing
and no `(ticket, token)` pair sorts before its own. This is the bakery-lock
algorithm; tokens break ties identically in both languages. JSON is published
through `.<token>.tmp` followed by atomic rename, so readers never observe a
half-written claim. Ticket/PID values must be positive safe integers (ticket 0
is the sole choosing exception), tokens must match filenames, and every owner
field must be well formed.

A writer removes only its own unique claim on completion or error. It never
removes the shared directory. A claim left by a crashed process can be removed
only on POSIX, when its hostname and PID scope match the current process and a
process probe proves its PID no longer exists. Windows and other unsupported
platforms never signal an owner or automatically reclaim its claim; an
abandoned claim there requires manual inspection/removal after owners stop. Linux scope identifies `/proc/self/ns/pid`
so another container's live PID cannot be mistaken for a dead local process.
Other platforms use their platform identifier. Missing namespace information,
permission-denied probes, foreign owners and malformed records are not evidence
of death and are never automatically stolen. Unique filenames avoid the race
where two stale-lock reapers accidentally remove a replacement owner's lock.

Lock acquisition waits at most two seconds (apart from filesystem call latency).
Contention or an unidentifiable abandoned claim then fails the cache flush:
Node rejects `flush()`; Python logs a warning, retaining its existing API contract.
`filterRelevant`/`filter_relevant` already log cache failures and continue the
classification result. Unknown/foreign abandoned records require an operator to
inspect them; remove them only after establishing that their owners have stopped.
Temporary files left by a killed process do not block later claims.

## Compatibility limits

This coordination protocol requires all concurrent writers to be upgraded.
Older clients can still read the version-1 file, but an older writer ignores the
claims and can still overwrite newer entries. Network/distributed filesystems,
cache-path aliases, and external deletion or modification of active claims are
outside this local-filesystem coordination contract. A loaded instance is an
in-memory snapshot; reopen it (or successfully flush local changes) to see other
writers' new records.

Node also serializes flush calls on the same instance. A `put` during async I/O
stays dirty until a subsequent flush, so an older pending flush cannot erase it.
Python keeps its existing per-instance thread lock around each flush.

The Node regression suite can run an actual mixed-language process test with
`PATCHLING_PY_SOURCE` pointing to a Python source checkout and
`PATCHLING_PYTHON` selecting its Python interpreter. Without that optional
checkout, independent-process coverage still runs and the mixed-language check
is explicitly skipped; tests never fetch a repository or call an inference API.
