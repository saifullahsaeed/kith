# Kith — one place to find every way of running this thing.
#
#   make server        the backend, natively on 127.0.0.1:8611 (also serves the UI)
#   make ui            build the interface
#   make desktop       build and launch the Mac app
#   make cli           put `kith` on PATH, pointing at this checkout
#
# There is no Docker Compose stack any more. The server used to run in a container
# with nginx in front of it; both were removed once the server started serving the
# UI itself — nginx was serving the same build at a second port for no benefit.
#
# Docker is not needed at all. This used to say it was still required for Kith's
# sandbox and to point at infra/sandbox.py; that module was deleted when the
# sandbox was replaced by infra/permissions.py, and the sentence outlived it by
# months. Docker is now only a fallback route to a SearXNG instance for web
# search, which is optional — see desktop/RELEASE.md.

SHELL := /bin/bash
ROOT  := $(shell pwd)
PY    := $(ROOT)/server/.venv/bin/python
PIP   := $(ROOT)/server/.venv/bin/pip

# Native config. Every one of these fails quietly if wrong, which is why they are
# set here rather than left to a shell someone forgot to export.
export KITH_UI_DIST        := $(ROOT)/ui/dist
export OLLAMA_HOST         := http://127.0.0.1:11434
export KITH_SEARCH_URL     := http://127.0.0.1:8888
export KITH_TZ             := Asia/Riyadh
export KITH_SEARCH_PROVIDER := auto
export KITH_OR_PROVIDER    := DeepSeek

.PHONY: help venv ui server desktop dev cli lint test check shipped clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

venv: ## create/refresh the server virtualenv
	@test -d server/.venv || python3 -m venv server/.venv
	@$(PIP) install -q -r server/requirements.txt
	@$(PIP) install -q -r server/requirements-dev.txt
	@echo "venv ready: $$($(PY) --version)"

ui: ## build the interface (the server and the desktop app both serve this output)
	@cd ui && npm run build

server: venv ## run the backend natively — serves the API and the UI on :8611
	@echo "serving UI from $(KITH_UI_DIST)"
	@cd server && $(PY) app.py

desktop: ui ## build and launch the Mac app (expects `make server` running)
	@cd desktop && npm run build && npx electron .

dev: ui ## desktop app with the window-chrome self-check
	@cd desktop && npm run build && npx electron . --dev

cli: venv ## install the `kith` command into ~/.local/bin
	@PYTHONPATH=$(ROOT)/server $(PY) -m kith.cli install $(ARGS)

lint: ## ruff (server), oxlint (ui) — the fast pass, not the gate
	@$(PY) -m ruff check server/kith
	@cd ui && npm run lint

test: venv ## run the server test suite
	@cd server && $(PY) -m pytest -q

# Delegates rather than re-listing the steps, which is the whole reason `./check` exists. This
# target used to be `lint test` and describe itself as "everything CI would run", and both CI
# workflows run `./check` — so the two drifted in the direction nobody noticed: no pyright, no
# vitest, no vite build, and a desktop `tsc --noEmit` that reads a different config from the
# real build and passed clean on a tree that failed to compile.
check: venv ## everything CI would run — the real gate
	@./check

shipped: venv ## regenerate server/shipped.json from the release tags (before cutting a release)
	@cd server && $(PY) scripts/record_shipped.py

clean: ## drop build output and the desktop app's saved window geometry
	@rm -rf ui/dist desktop/out
	@rm -f "$$HOME/Library/Application Support/kith-desktop/window-state.json"
