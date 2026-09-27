# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed (breaking)
- **SDD structure migrated** to `specs/<feature>/spec.md` + `design.md` (one directory per
capability). The previous flat `specs/*.spec.md` layout and the `changes/` staging
  directory are **removed**; the retired proposal flow (five stage commands) is no longer used.
  The two existing proposals were folded in: the deployment proposal into
  `specs/deployment/design.md`, the audit-fixes proposal into `specs/audit-fixes/spec.md`
  (marked archived). `docs/sdd-workflow.md` rewritten as the five-stage process view.
- **Guidance assets added**: `.claude/{agents,skills,rules,workflows}` (9 / 19 / 8 / 8) with
  `.qoder/{agents,skills}` as a one-way mirror; `CLAUDE.md` rewritten as the agent entry
  point (its previous content — a one-off scaffold prompt — moved to
  `docs/archive/build-task-scaffold.md`); new `AGENTS.md` for non-Claude agents.
- **New hard-constraint rules**: `obb-hard-constraints.md` (promotes the scaffold's
  never-violate list) and `frontend-backend-contract.md` (contract changes must land in one
  commit across `api_server.py` + `types/index.ts` + `services/api.ts`).
- **Gates added**: `scripts/sync_agent_assets.py` and `scripts/check_assets.py` (7 checks,
  including a legacy-stack terminology gate and an SDD structure gate), `.githooks/pre-commit`,
  Makefile targets `assets-sync` / `assets-check` / `hooks-install`, and an asset-consistency
  step in CI. `make lint` / `make type` now also cover `scripts/`.
- **`.gitattributes` added** (not in the original plan, but required): `core.autocrlf=true`
  with no attribute file made the pre-commit shebang resolve as `bash\r`, so the hook could
  not run on any platform. Hooks and shell scripts are pinned to `eol=lf`.

### Added
- Docker self-hosting: root `Dockerfile` (CPU-only backend), `gui/Dockerfile`
  (Vite build + nginx `/api` reverse proxy), `docker-compose.yml`, `.env.example`,
  `.dockerignore`, `Makefile`.
- Central runtime configuration (`src/config.py`) with precedence
  env > `config/global.yaml` > defaults (`VL_ANCHOR_*`, `VL_MODEL_*`).
- Configurable model backend: `OpenAICompatClient` for a remote OpenAI-compatible
  VL/LLM endpoint (`provider: remote`) alongside the offline deterministic stub
  (`provider: stub`, default). Removes any hard dependency on bundled weights.
- `/api/health` liveness endpoint; optional bearer-token auth middleware
  (`VL_ANCHOR_AUTH_TOKEN`); CORS origins now env-driven.
- GitHub Actions: `ci.yml` (ruff/mypy/pytest + GUI typecheck/build),
  `docker.yml` (build, and push to GHCR on `v*`/main), `release.yml`.
- `LICENSE` (MIT), `SECURITY.md`, Docker deployment section in `README.md`.
- Platform onboarding: `run.py doctor [--deep] [--json]` self-check (config
  completeness, per-role endpoint connectivity, storage/prompt/secret-store
  state), a fifth GUI tab holding the settings form, and an always-on banner
  warning when `provider: stub` is producing placeholder boxes.
- Config API + write-back layer: `GET/PUT /api/config`, `POST /api/config/test`,
  `GET /api/diagnostics`; `config/overrides.yaml` sits between env and
  `global.yaml`, every field reports its source (`env`/`overrides`/`yaml`/
  `secrets`/`default`) and whether an env var has locked it; `global.yaml` is
  never rewritten (its comments survive).
- Encrypted secret store `config/secrets.db` (Fernet, master key only from
  `VL_ANCHOR_SECRET_KEY[_FILE]`), `run.py secret-keygen` (prints, writes
  nothing), `run.py secret-set NAME` (value read from stdin). API keys can no
  longer reach disk in plaintext — an `api_key` in YAML is ignored and warned
  about. Without a master key the store is simply unavailable and the platform
  runs on env keys.
- Metadata & audit logs: append-only `tasks/<name>/run_history.jsonl`,
  `tasks/<name>/model_calls.jsonl` and `logs/diagnostics.jsonl` on disk as the
  source of truth, plus `index.db` as a rebuildable SQLite projection
  (`run.py index --rebuild`) behind `GET /api/index/stats`. Task history reads
  the disk logs directly (`GET /api/tasks/{name}/history`) and works with no
  database at all.
- GUI: `Authorization: Bearer` injection (`VITE_AUTH_TOKEN` build-time, then
  localStorage), execution history on the inspection page, settings page with
  per-field source badges and connection tests.

### Fixed
- CORS preflight (`OPTIONS`) is no longer rejected by the bearer-token
  middleware: browsers never attach `Authorization` to a preflight, so
  `PUT /api/config` could not be sent at all on a deployment that enabled
  `VL_ANCHOR_AUTH_TOKEN`. Regression-covered in `tests/test_api_server.py`.
- `index --rebuild` now empties all four data tables before replaying the disk
  logs instead of relying on `UNIQUE` + `INSERT OR IGNORE` alone, which left
  orphaned rows behind when log lines disappeared (task removed, JSONL
  truncated) and made the index drift from its source.
- `start_gui.ps1` / `start_gui.sh` probe `/api/health` instead of `/api/tasks`
  for the backend health check — the latter returns 401 once auth is enabled,
  which aborted startup on a correctly configured deployment. Both scripts now
  also run `doctor` and print a warning without blocking startup.

### Fixed (audit follow-ups)
- CORS + relative `/api` in dev so the GUI actually receives backend data.
- Path-traversal guards on task/image names and non-mutating `task_dir`.
- Robust label parsing (BOM, non-UTF-8 -> replace, out-of-range/unknown class
  filtered), and `source` field surfaced to the canvas.

## [0.1.0]
- Initial scaffold: plan/annotate/inspect/split pipeline, FastAPI backend,
  CLI (`run.py`), Tauri + React + Ant Design GUI, pytest suite, specs.
