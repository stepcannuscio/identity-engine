# Identity Engine — Claude Code Instructions

These instructions apply to every task in this project.
Follow them without being asked.

## After every implementation task

When you finish implementing any feature, run these steps
in order before declaring the task complete. Do not skip
any step. Do not ask permission to run them.

### Step 1 — lint and type check

Run all of the following and fix every error and warning
before proceeding. Do not proceed if any of these fail.

    .venv/bin/python -m flake8 . \
        --exclude=.venv,__pycache__ \
        --max-line-length=100

    .venv/bin/python -m mypy . \
        --ignore-missing-imports \
        --exclude .venv

    .venv/bin/pyright --pythonpath .venv/bin/python

If flake8, mypy, or pyright are not installed, install
them into the venv first, add them to requirements.txt,
then run.

Fix all errors first, then all warnings. Do not suppress
warnings with noqa or type: ignore comments unless there
is a genuine false positive — explain why in a comment
if you do.

### Step 2 — tests

Run the full test suite:

    make test

All tests must pass. If any test fails because of your
changes, fix the code, not the test, unless the test
itself is wrong — in which case explain why before
changing it.

If you added new functionality, add tests for it before
this step. New code without tests is not complete.

### Step 3 — docs

For every file you created or meaningfully changed:

- If it is a new module: add a docstring at the top of
  the file explaining what it does and how to use it
- If it is an existing module: update the docstring if
  the behaviour changed
- If it adds or changes a public function or class:
  update or add a docstring on that function/class
- If it changes behaviour documented in docs/: update
  the relevant .md file

### Step 4 — README

If your changes affect any of the following, update
README.md accordingly:

- How to set up the project
- How to run the project
- What the project can now do that it could not before
- New make targets
- New environment or keychain configuration required

The README must always reflect the current state of the
project. A developer (or future you) reading it cold
should be able to get the project running.

### Step 5 — completion report

When all four steps are clean, report:

  lint:   pass
  types:  pass
  tests:  N passed
  docs:   updated (list files changed)
  readme: updated | no changes needed

Do not report complete until all five lines are green.

## General coding standards

- All database access through db/connection.py only
- All keychain access through config/settings.py only
- All LLM inference through config/llm_router.py only
- Never hardcode model names, key names, or paths outside
  their canonical definition files
- Every new module gets a top-level docstring
- Functions longer than 40 lines should be split unless
  there is a clear reason not to
- No print statements in library code — use logging
- Scripts (scripts/) may use print for user-facing output
- When committing, never add Co-authored-by, Generated-by, or any trailer
lines to commit messages.

## Security rules

- Never write the database file inside the project directory
- Never log or print key material of any kind
- Never add a file containing real personal data to git
- If you are unsure whether something is sensitive, treat
  it as sensitive
  