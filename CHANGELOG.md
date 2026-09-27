# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
