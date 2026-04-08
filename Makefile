VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
PYTEST := $(VENV)/bin/pytest
PRE_COMMIT := $(VENV)/bin/pre-commit

.PHONY: help setup init test clean interview capture query view serve smoke \
	add-anthropic-key add-groq-key set-ui-passphrase

## Show this help message
help:
	@echo "identity-engine — available targets:"
	@echo ""
	@echo "  make setup   Create .venv, install dependencies, install pre-commit hooks"
	@echo "  make init    Run scripts/init_db.py to initialise the encrypted database"
	@echo "  make test    Run the pytest test suite with verbose output"
	@echo "  make clean   Remove .venv and __pycache__ (never removes the database)"
	@echo "  make capture Write a quick capture directly to the identity store"
	@echo "  make query   Start an interactive freeform query session"
	@echo "  make serve   Start the HTTPS FastAPI backend server"
	@echo "  make smoke   Run the Python smoke test against the backend"
	@echo "  make view    Pretty-print the identity store grouped by domain"
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

## Run the test suite (depends on setup)
test: setup
	@echo "--> Running tests..."
	$(PYTEST) tests/ -v

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

## Phase 2 placeholder
run:
	@echo "Application layer not yet implemented."

## Run the interactive identity interview
interview:
	.venv/bin/python scripts/seed_interview.py

## Write a quick capture directly to the identity store
capture:
	.venv/bin/python scripts/capture.py --text "$(TEXT)" \
	$(if $(DOMAIN),--domain $(DOMAIN),)

## Run an interactive freeform query session
query:
	.venv/bin/python scripts/query.py

## Run the HTTPS FastAPI backend server
serve:
	.venv/bin/python scripts/serve.py

## Run the Python smoke test against the HTTPS backend
smoke:
	.venv/bin/python scripts/smoke_api.py

## Pretty-print the identity store grouped by domain
view:
	.venv/bin/python scripts/view_db.py

## Store an Anthropic API key in the system keychain
## Usage: make add-anthropic-key KEY=sk-ant-...
add-anthropic-key:
	.venv/bin/python -c "import keyring, sys; \
	keyring.set_password('identity-engine', \
	'anthropic-api-key', sys.argv[1])" $(KEY)

## Store a Groq API key in the system keychain
## Usage: make add-groq-key KEY=gsk_...
add-groq-key:
	.venv/bin/python -c "import keyring, sys; \
	keyring.set_password('identity-engine', \
	'groq-api-key', sys.argv[1])" $(KEY)

## Update the UI passphrase stored in the system keychain
set-ui-passphrase:
	.venv/bin/python -c "\
	import keyring, getpass; \
	p = getpass.getpass('New UI passphrase (min 12 chars): '); \
	assert len(p) >= 12, 'Too short'; \
	keyring.set_password('identity-engine', 'ui-passphrase', p); \
	print('Passphrase updated.')"
