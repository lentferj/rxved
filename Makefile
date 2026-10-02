# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: Copyright (C) 2026  rxved contributors
#
# rxved's quality checks. `make check` is what CI would run; everything else
# is a piece of it you can run on its own.
#
# All tools come from the project's own .venv, pinned in pyproject.toml's
# [project.optional-dependencies].dev, so the versions that decide whether a
# commit is acceptable are the same on every machine.

# Where the venv keeps its console scripts: Windows uses .venv/Scripts,
# everything else .venv/bin. CI runs `make check` on Linux, macOS and
# Windows from this same file, so the layout cannot be assumed POSIX.
VENV_BIN := $(shell [ -d .venv/Scripts ] && echo .venv/Scripts || echo .venv/bin)
PYTHON ?= $(VENV_BIN)/python

RUFF        := $(VENV_BIN)/ruff
MYPY        := $(VENV_BIN)/mypy
PYTEST      := $(VENV_BIN)/pytest
PIP_AUDIT   := $(VENV_BIN)/pip-audit
VULTURE     := $(VENV_BIN)/vulture
DEPTRY      := $(VENV_BIN)/deptry
DETECT_SECRETS := $(VENV_BIN)/detect-secrets

# detect-secrets baseline handling: a scratch copy, and a comparator that
# ignores `generated_at` so a clean scan does not dirty the tree.
SECRETS_TMP   := .secrets.baseline.tmp
SECRETS_DIFF  := tools/diff_secrets_baseline.py

# Dead code: 80% is vulture's own suggestion and the point at which it stops
# reporting Textual's dispatch methods (on_mount, action_*, CSS, BINDINGS) as
# unused. Lower it and every framework callback becomes a finding.
VULTURE_CONFIDENCE := 80

# Paths, everywhere. Keeps the tools off .venv and the generated catalog.
SOURCES := rxved xv tools tests

.DEFAULT_GOAL := check
.PHONY: check lint format format-check typecheck typecheck-strict \
        test audit audit-deps audit-dead audit-secrets baseline \
        strict-modules help

help:  ## List the targets
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sed -E 's/^([a-z-]+):.*## (.*)$$/\1\t\2/' \
		| awk -F'\t' '{printf "  %-18s %s\n", $$1, $$2}'

# --- the whole pipeline ------------------------------------------------------

check: lint format-check typecheck test audit  ## Run everything, failing on any error
	@echo
	@echo "all checks passed"

# --- lint and format ---------------------------------------------------------
#
# format-check is part of `check`, not optional: the point of landing the
# formatting as its own commit (b13e1c0) was to be able to enforce it
# afterwards, without a gate that fails on arrival.

lint: ## ruff: lint (no autofix)
	$(RUFF) check $(SOURCES)

format: ## ruff: format in place
	$(RUFF) format $(SOURCES)
	$(RUFF) check --fix $(SOURCES)

format-check: ## ruff: is anything unformatted?
	$(RUFF) format --check $(SOURCES)

# --- types -------------------------------------------------------------------

typecheck: ## mypy, non-strict (see pyproject.toml for what is enabled)
	$(MYPY)

typecheck-strict: ## mypy --strict, reporting per module so the list shrinks
	@echo "modules not yet strict-clean (target: none left):"
	@for m in $$(find rxved xv tools -name '*.py' | sort); do \
		n=$$($(MYPY) --strict --no-pretty --no-error-summary "$$m" 2>/dev/null \
			| grep -c 'error:' || true); \
		if [ "$$n" -ne 0 ]; then printf '  %-32s %s errors\n' "$$m" "$$n"; fi; \
	done
	@echo "(no output above means every module is strict-clean)"

strict-modules: ## Add strict = true for a module to pyproject.toml
	@echo "Edit the [tool.mypy.overrides] block whose module list matches,"
	@echo "then run 'make typecheck' to confirm."

# --- tests -------------------------------------------------------------------

test: ## pytest with coverage, no threshold yet
	$(PYTEST)

# --- audit -------------------------------------------------------------------

audit: audit-deps audit-dead audit-secrets  ## Dependencies, dead code, secrets

# pip-audit is scoped to THIS venv's site-packages. The venv was created with
# --system-site-packages (see README), so an unscoped run audits the whole
# Debian userland -- every distro package on the box -- and reports CVEs in
# brlapi, terminator, libtorrent and so on that have nothing to do with rxved.
SITE_PACKAGES := $(shell $(PYTHON) -c \
	'import sysconfig; print(sysconfig.get_paths()["purelib"])')

# Three setuptools advisories, ignored ONLY in a --system-site-packages
# venv, and only for that package's sake. In such a venv setuptools comes
# from Debian (66.1.1 here) and cannot be upgraded: setuptools>=70 imports
# `splat` from jaraco.functools, and /usr/lib/python3/dist-packages wins
# over the venv's own site-packages, so installing a newer jaraco.functools
# does not shadow it and the upgrade installs and then breaks the
# interpreter. Verified, not assumed.
#
# Scoped to the flag rather than passed unconditionally on purpose: a CI
# runner builds a clean venv, has a modern setuptools, needs no ignores at
# all, and so audits with nothing suppressed. Rebuild the venv without
# --system-site-packages and these stop applying by themselves.
#
# Re-check these IDs rather than trusting the list: they are pinned to a
# version that moves under Debian.
SHARES_SYSTEM_SITE := $(shell grep -q '^include-system-site-packages = true' \
	.venv/pyvenv.cfg 2>/dev/null && echo yes || echo no)
ifeq ($(SHARES_SYSTEM_SITE),yes)
PIP_AUDIT_IGNORES := --ignore-vuln PYSEC-2025-49 \
                     --ignore-vuln PYSEC-2026-1918 \
                     --ignore-vuln PYSEC-2026-3447
else
PIP_AUDIT_IGNORES :=
endif

audit-deps: ## pip-audit: known CVEs in this project's dependencies
	$(PIP_AUDIT) --progress-spinner off \
		--path $(SITE_PACKAGES) \
		$(PIP_AUDIT_IGNORES)

audit-dead: ## vulture + deptry: unreachable code, unused/missing deps
	$(VULTURE) --min-confidence $(VULTURE_CONFIDENCE) $(SOURCES)
	$(DEPTRY) .

# Runs against a *copy* of the baseline and diffs it, rather than letting
# detect-secrets rewrite the tracked one. `scan --baseline` refreshes
# `generated_at` on every run even when nothing was found, so the obvious
# spelling leaves the working tree dirty after every `make check` and
# turns every subsequent commit into a one-line diff that means nothing.
#
# Exits non-zero only on a real difference: a new secret, a removed
# finding, or a changed plugin/filter list. `generated_at` is ignored.
audit-secrets: ## detect-secrets against .secrets.baseline
	@set -e; \
	trap 'rm -f $(SECRETS_TMP)' EXIT; \
	cp .secrets.baseline $(SECRETS_TMP); \
	$(DETECT_SECRETS) scan --baseline $(SECRETS_TMP) >/dev/null; \
	$(PYTHON) $(SECRETS_DIFF) .secrets.baseline $(SECRETS_TMP)

# --- maintaining the baseline ------------------------------------------------

baseline: ## Regenerate .secrets.baseline, then show what changed
	@cp .secrets.baseline .secrets.baseline.prev 2>/dev/null || true
	$(DETECT_SECRETS) scan > .secrets.baseline
	@echo "regenerated .secrets.baseline"
	@echo "previously-recorded findings:"
	@$(PYTHON) -c "import json,sys; \
		old=json.load(open('.secrets.baseline.prev'))['results'] \
			if __import__('os').path.exists('.secrets.baseline.prev') else {}; \
		[print('  %s:%s' % (f, s['line_number'])) \
		 for f, items in old.items() for s in items]" 2>/dev/null || true
	@echo "newly-recorded findings (review each before committing):"
	@$(PYTHON) -c "import json; \
		print('\n'.join('  %s:%s  %s' % (f, s['line_number'], s['type']) \
		 for f, items in json.load(open('.secrets.baseline'))['results'].items() \
		 for s in items))"
	@rm -f .secrets.baseline.prev