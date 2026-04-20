VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
PRE_COMMIT := $(VENV)/bin/pre-commit
PYRIGHT := $(VENV)/bin/pyright
NPM := npm
FRONTEND_DIR := frontend
FRONTEND_NODE_MODULES := $(FRONTEND_DIR)/node_modules/.package-lock.json

APP ?= all
BACKEND_ARGS ?=
FRONTEND_ARGS ?=

.PHONY: help setup init test test-backend test-frontend typecheck verify-backend clean \
	capture query view serve smoke add-anthropic-key add-groq-key \
	set-ui-passphrase frontend-install frontend-dev frontend-build dev

## Show this help message
help:
	@echo "identity-engine — available targets:"
	@echo ""
	@echo "  make setup   Create .venv, install dependencies, install pre-commit hooks"
	@echo "  make init    Run scripts/init_db.py to initialise the encrypted database"
	@echo "  make test    Run backend + frontend tests by default"
	@echo "               Use APP=backend|frontend|all BACKEND_ARGS='...' FRONTEND_ARGS='...'"
	@echo "  make typecheck  Run backend Pyright type-checking from .venv"
	@echo "  make verify-backend  Run compileall, backend tests, and Pyright from .venv"
	@echo "  make clean   Remove .venv and __pycache__ (never removes the database)"
	@echo "  make capture Write a quick capture directly to the identity store"
	@echo "  make query   Start an interactive freeform query session"
	@echo "  make serve   Start the HTTPS FastAPI backend server"
	@echo "  make smoke   Run the Python smoke test against the backend"
	@echo "  make view    Pretty-print the identity store grouped by domain"
	@echo "  make frontend-install  Install frontend npm dependencies"
	@echo "  make frontend-dev  Start the Vite frontend dev server"
	@echo "  make frontend-build  Build the production frontend bundle"
	@echo "  make dev     Start the backend and frontend together"
	@echo "  make set-ui-passphrase  Update the web UI passphrase in the keychain"
	@echo ""

## Create .venv, install requirements, install pre-commit hooks
setup: $(VENV)/bin/activate

$(VENV)/bin/activate:
	@echo "--> Creating virtual environment..."
	python3 -m venv $(VENV)
	@echo "--> Installing dependencies..."
	$(PIP) install --quiet --upgrade pip
	$(PIP) install --quiet -r requirements.txt
	@echo "--> Installing pre-commit hooks..."
	$(PRE_COMMIT) install
	@echo "Setup complete."

## Initialise the encrypted database (depends on setup)
init: setup
	@echo "--> Initialising database..."
	$(PYTHON) scripts/init_db.py

$(FRONTEND_NODE_MODULES): $(FRONTEND_DIR)/package.json $(FRONTEND_DIR)/package-lock.json
	@echo "--> Installing frontend dependencies..."
	cd $(FRONTEND_DIR) && $(NPM) install

## Run the full test suite or a scoped app-specific subset
test:
	@case "$(APP)" in \
		all) \
			$(MAKE) test-backend BACKEND_ARGS="$(BACKEND_ARGS)"; \
			$(MAKE) test-frontend FRONTEND_ARGS="$(FRONTEND_ARGS)"; \
			;; \
		backend) \
			$(MAKE) test-backend BACKEND_ARGS="$(BACKEND_ARGS)"; \
			;; \
		frontend) \
			$(MAKE) test-frontend FRONTEND_ARGS="$(FRONTEND_ARGS)"; \
			;; \
		*) \
			echo "Unknown APP='$(APP)'. Use APP=all, APP=backend, or APP=frontend."; \
			exit 1; \
			;; \
	esac

## Run the backend pytest suite
test-backend: setup
	@echo "--> Running backend tests..."
	$(PYTHON) -m pytest tests/ -v $(BACKEND_ARGS)

## Run backend Pyright type-checking
typecheck: setup
	@echo "--> Running backend type checks..."
	$(PYTHON) -m pyright

## Run backend compile, tests, and type-checking
verify-backend: setup
	@echo "--> Compiling backend modules..."
	$(PYTHON) -m compileall engine tests
	@echo "--> Running backend tests..."
	$(PYTHON) -m pytest tests/ -v $(BACKEND_ARGS)
	@echo "--> Running backend type checks..."
	$(PYTHON) -m pyright

## Run the frontend Vitest suite
test-frontend: $(FRONTEND_NODE_MODULES)
	@echo "--> Running frontend tests..."
	cd $(FRONTEND_DIR) && $(NPM) run test -- --reporter=verbose $(FRONTEND_ARGS)

## Remove .venv and __pycache__ (never removes the database or keychain entry)
clean:
	@echo "WARNING: This will delete .venv/ and all __pycache__ directories."
	@echo "         The database at ~/.identity-engine/ and the keychain entry will NOT be removed."
	@read -p "Continue? [y/N] " confirm && [ "$$confirm" = "y" ] || exit 1
	@echo "--> Removing .venv/..."
	rm -rf $(VENV)
	@echo "--> Removing __pycache__ directories..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	@echo "Clean complete."

## Write a quick capture directly to the identity store
capture:
	$(PYTHON) scripts/capture.py --text "$(TEXT)" \
	$(if $(DOMAIN),--domain $(DOMAIN),)

## Run an interactive freeform query session
query:
	$(PYTHON) scripts/query.py

## Run the HTTPS FastAPI backend server
serve:
	$(PYTHON) scripts/serve.py

## Install frontend npm dependencies
frontend-install: $(FRONTEND_NODE_MODULES)

## Start the Vite frontend dev server
frontend-dev:
	cd $(FRONTEND_DIR) && $(NPM) run dev

## Build the production frontend bundle
frontend-build:
	cd $(FRONTEND_DIR) && $(NPM) run build

## Start the backend and frontend together
dev:
	$(MAKE) serve & $(MAKE) frontend-dev

## Run the Python smoke test against the HTTPS backend
smoke:
	$(PYTHON) scripts/smoke_api.py

## Pretty-print the identity store grouped by domain
view:
	$(PYTHON) scripts/view_db.py

## Store an Anthropic API key in the system keychain
## Usage: make add-anthropic-key KEY=sk-ant-...
add-anthropic-key:
	$(PYTHON) -c "import keyring, sys; \
	keyring.set_password('identity-engine', \
	'anthropic-api-key', sys.argv[1])" $(KEY)

## Store a Groq API key in the system keychain
## Usage: make add-groq-key KEY=gsk_...
add-groq-key:
	$(PYTHON) -c "import keyring, sys; \
	keyring.set_password('identity-engine', \
	'groq-api-key', sys.argv[1])" $(KEY)

## Update the UI passphrase stored in the system keychain
set-ui-passphrase:
	$(PYTHON) -c "\
	import keyring, getpass; \
	p = getpass.getpass('New UI passphrase (min 12 chars): '); \
	assert len(p) >= 12, 'Too short'; \
	keyring.set_password('identity-engine', 'ui-passphrase', p); \
	print('Passphrase updated.')"
