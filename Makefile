# Convenience targets for local dev and Docker self-hosting.
.PHONY: help lint type test doctor gui-install gui-typecheck gui-build \
        docker-build up down logs ps clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

lint: ## Ruff over backend, CLI and tests
	uv run ruff check src/ run.py tests/

type: ## mypy (strict) over the backend package
	uv run mypy src/

test: ## Run the pytest suite
	uv run pytest -q

doctor: ## Self-check the configuration (exit 1 when a check fails)
	uv run python run.py doctor

gui-install: ## Install GUI node modules
	cd gui && npm install

gui-typecheck: ## TypeScript check for the GUI
	cd gui && npx tsc --noEmit

gui-build: ## Production build of the GUI SPA
	cd gui && npm run build

docker-build: ## Build both images via compose
	docker compose build

up: ## Start the stack detached (builds if needed)
	docker compose up -d --build

down: ## Stop the stack
	docker compose down

logs: ## Tail compose logs
	docker compose logs -f

ps: ## Show compose service status
	docker compose ps

clean: ## Remove caches and the local GUI build
	rm -rf .pytest_cache .mypy_cache .ruff_cache gui/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
