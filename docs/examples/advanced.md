# Advanced Examples

> **Patchling everywhere:** this library also ships for browser and Node as [patchling](https://github.com/255BITS/patchling) — see it running live in [nanoodle.com](https://nanoodle.com), a no-server visual AI workflow editor built on it.

## Database Layer Migration

```bash
patchling "Replace raw SQL with SQLAlchemy ORM" \
    models/ queries/ \
    --apply
```

## API Versioning

```bash
patchling "Add v2 API endpoints with backward compatibility" \
    --model deepseek-reasoner \
    --temperature 0.5 \
    --apply
```

**Created Files:**
- `api/v2/schemas.py`
- `api/v2/routers/`
- `tests/v2/`

## Internationalization

```bash
patchling "Extract all UI strings to translation files" \
    --files templates/ static/js/ \
    --call
```

Saved in patch.diff

---

## Take It Further: Agent Loops

These advanced transformations become even more powerful when run continuously. Instead of a single pass, let Patchling iterate until the job is done:

```bash
# Complete migration in multiple passes
while true; do
  patchling "Continue migrating raw SQL to SQLAlchemy ORM" \
    models/ queries/ \
    --apply
  sleep 5
done
```

Each cycle catches what the previous one missed—edge cases, forgotten files, consistency fixes. Complex refactors that would take days of manual work complete themselves overnight.

**Real Results:** A database migration loop running over 72 hours:

| Metric | Single Pass | After Agent Loop |
|--------|-------------|------------------|
| Tables migrated | 12 | 47 |
| Raw SQL queries remaining | 89 | 3 |
| Test coverage for ORM | 15% | 82% |

[Learn more about Agent Loops →](automation.md)
