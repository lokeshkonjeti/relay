# Contributing

## Packs

A pack is a JSON file in `plugins/relay/packs/` that defines task categories for a kind of
project. It may contain `name`, `description`, `tasks` and `roles`, never `connections`.

```json
{
  "name": "data-eng",
  "description": "Pipelines and warehouses: Claude owns modelling; delegates DAG boilerplate, tests and docs.",
  "tasks": {
    "data-model": {"kind": "read", "route": ["self"], "desc": "warehouse schema and grain"},
    "dag-scaffold": {"kind": "write", "route": ["@agent", "@cheap"], "desc": "Airflow/Dagster DAG from a decided design"},
    "dbt-docs": {"kind": "write", "route": ["@cheap"], "desc": "dbt model docs and tests"}
  }
}
```

Guidelines:
- Route judgment work (design, security, debugging) to `self`.
- Use roles (`@reader`, `@cheap`, `@agent`, `@verifier`) rather than connection names, so the pack
  works for everyone.
- Keep `desc` under 8 words. Claude reads it every time it delegates.

## Connection presets

Add to `plugins/relay/lib/relay_core/presets.json`. Default `data` to `unverified` unless the
provider is local (`local`) or known to train or retain on the tier in question (`may-train`).

## Tests

`python3 -m unittest discover -s tests -v` must pass. New behaviour needs a test in
`tests/test_relay.py`; the fake agent in `tests/fakes/` can impersonate any CLI.
