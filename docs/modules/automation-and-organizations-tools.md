# Module: automation / organizations tools (`src/ninjaone_mcp/tools/`)

Accumulated, cross-ticket facts about these two tool-registration modules. See the sibling
`.zh.md` for the Chinese version (content must match). Append a new dated section per
ticket; never rewrite an earlier section.

## PRD-19905 · 2026-10-09

Anchor: `dev01.mbagent/PRD-19905@8aca01d` — **not yet merged to `main`** (work branch pushed,
awaiting manual merge; see the ticket doc's Handoff section). Re-verify these path:line claims
once merged, since line numbers can shift.

- `automation.py`'s `ninjaone_get_automation_scripts` and `ninjaone_get_device_scripting_options`
  take an optional `name_filter` (case-insensitive substring, applied client-side after fetch) —
  `src/ninjaone_mcp/tools/automation.py:77-106` and `:108-130`. `ninjaone_get_automation_scripts`
  additionally takes optional `limit`/`after`, forwarded to the upstream call **only when the
  caller supplies them** — the bare no-args call is asserted byte-identical to the pre-PRD-19905
  behavior (test: `tests/test_automation.py::test_no_filter_preserves_existing_truncation_behavior`).
- `_filter_named_lists` (`src/ninjaone_mcp/tools/automation.py:52-61`) filters **every top-level
  list-of-dicts field whose items carry a `name` key**, not a hardcoded key like `"scripts"` —
  this was a deliberate choice because the real response shape of
  `GET /device/{id}/scripting/options` (which keys hold scripts vs. actions) was never confirmed
  against a live NinjaOne tenant. If a future ticket gets live API access and confirms the real
  key names, this is a candidate to simplify to a direct key lookup.
- `organizations.py` gained `ninjaone_get_organization_custom_fields(organization_id)` —
  `src/ninjaone_mcp/tools/organizations.py:114-138`. **The upstream endpoint path
  (`/api/v2/organization/{id}/custom-fields`) is unverified against a live tenant** — it's the
  design doc's best guess, not a confirmed fact. If a live call later reveals a different path,
  only this one function needs to change.
- Tool count is 24 as of this ticket (was 23); `tests/test_tools.py:14-63`'s `EXPECTED_TOOLS`/
  `READ_ONLY_TOOLS` dicts are the enforced manifest — any new tool or new required/optional arg
  must be registered there or `test_tools_list_snapshot` fails.
- This repo has no `AGENTS.md`/`CLAUDE.md` and no `[tool.mypy]`/`[tool.ruff]` in `pyproject.toml`
  — `uv run pytest` is the only declared/enforced gate (confirmed via `tdd.py detect` at the time
  of this ticket).
- Deploying these two changes requires a manual `vendor-deploy.sh update ninjaone` run in
  `mcp-service-gateway` after this branch merges to `main` — `vendor/registry.yaml` has no
  ref/commit pin for NinjaOne, so a running container won't pick these up automatically.
