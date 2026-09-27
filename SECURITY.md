# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.1.x   | Yes       |

## Reporting a vulnerability

Please report suspected vulnerabilities privately via GitHub
[security advisories](../../security/advisories/new) rather than a public issue.
Expect an acknowledgement within a few business days.

## Deployment hardening notes

This platform is designed to run either as a local desktop backend or as a
containerized self-hosted service. Keep the following in mind when exposing it
to a network:

- **Never expose the raw backend port publicly.** `docker-compose.yml` binds
  `127.0.0.1:8765` for the API and serves the UI through nginx on `:8080`.
  Put a TLS-terminating reverse proxy in front of the nginx service for real
  remote access; do not publish `8765` directly.
- **Enable API auth for multi-user / remote setups.** Set `VL_ANCHOR_AUTH_TOKEN`
  in `.env`; every `/api/*` call (except `/api/health`) then requires
  `Authorization: Bearer <token>`. When enabled behind nginx, inject the header
  in the proxy config or require clients to send it.
- **Model API keys.** `VL_MODEL_API_KEY` is read from the environment and is
  never written to logs or committed. `.env` is gitignored. Do not bake secrets
  into the image.
- **No telemetry.** The application makes no outbound calls except to the VL/LLM
  `base_url` and any model endpoint you explicitly configure.
- **CORS.** Set `VL_ANCHOR_CORS_ORIGINS` to the exact origins that must reach
  the API directly. With the bundled nginx `/api` proxy the UI is same-origin
  and CORS is not exercised.
- **File access.** Label/image endpoints are read-only and validate path
  parameters against traversal; originals are never overwritten (corrections go
  to `candidate_labels/`). Persist task data on the `vl-data` volume and restrict
  host permissions on it.

## Supply chain

- Python deps are locked via `uv.lock`; the Docker build installs from
  `uv export --frozen`.
- GUI deps install from `package-lock.json` (`npm ci`).
- Release workflow attaches SBOM and provenance to the published images
  (Docker Buildx `provenance: mode=max`, `sbom: true`).
