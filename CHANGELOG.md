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

### Fixed (audit follow-ups)
- CORS + relative `/api` in dev so the GUI actually receives backend data.
- Path-traversal guards on task/image names and non-mutating `task_dir`.
- Robust label parsing (BOM, non-UTF-8 -> replace, out-of-range/unknown class
  filtered), and `source` field surfaced to the canvas.

## [0.1.0]
- Initial scaffold: plan/annotate/inspect/split pipeline, FastAPI backend,
  CLI (`run.py`), Tauri + React + Ant Design GUI, pytest suite, specs.
