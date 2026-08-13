# Kith — one place to find every way of running this thing.
#
#   make server        the backend, natively on 127.0.0.1:8611 (also serves the UI)
#   make ui            build the interface
#   make desktop       build and launch the Mac app
#
# There is no Docker Compose stack any more. The server used to run in a container
# with nginx in front of it; both were removed once the server started serving the
# UI itself — nginx was serving the same build at a second port for no benefit.
#
# Docker is still needed for ONE thing: Kith's sandbox, the container that is his
# computer. It is created on demand by the server (see infra/sandbox.py), not by
# compose. Removing that dependency is the open question in desktop/PACKAGING.md.

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

.PHONY: help venv ui server desktop dev lint test check clean

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

lint: ## ruff (server), oxlint (ui), tsc (desktop)
	@$(PY) -m ruff check server/kith
	@cd ui && npm run lint
	@cd desktop && npx tsc --noEmit -p tsconfig.json

test: venv ## run the server test suite
	@cd server && $(PY) -m pytest -q

check: lint test ## everything CI would run

clean: ## drop build output and the desktop app's saved window geometry
	@rm -rf ui/dist desktop/out
	@rm -f "$$HOME/Library/Application Support/kith-desktop/window-state.json"
