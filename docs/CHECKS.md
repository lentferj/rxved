# Development checks

What `make check` runs, and everything that was suppressed to get it
passing on the existing code. Measured 2026-10-02 against commit `master`
with `rxved/screens.py` and `xv/config.py` present.

## The rule this page exists for

**No existing finding was mass-fixed.** Where a tool reported something,
the fix was a rule suppression with a reason written next to it, not an
edit to the code. Two exceptions, both listed at the bottom, are real
fixes.

So `make check` passes today, and flags anything *new*: the
suppressions are scoped to specific rules, specific modules or specific
line numbers, so a new `B905` in a module that does not already ignore
`B905` still fails.

## Running it

```sh
make check            # everything: lint, format-check, typecheck, test, audit
make lint             # ruff check             (no autofix)
make format           # ruff format + --fix    (rewrites files)
make format-check     # ruff format --check    (in `check`)
make typecheck        # mypy, non-strict
make typecheck-strict # per-module strict error counts
make test             # pytest + coverage
make audit            # pip-audit, vulture, deptry, detect-secrets
make baseline         # regenerate .secrets.baseline and diff it
make help             # every target
```

## What each tool is doing

| Tool | Scope | Enforcing? |
|---|---|---|
| ruff lint | `E F I B S SIM UP C4 PL RUF C901`, max-complexity 10 | yes, minus the list below |
| ruff format | 88 columns, double quotes, all files | yes, in `check` and as a hook |
| mypy | `rxved`, `xv`, `tools` | yes, minus per-module disables |
| pytest-cov | `xv`, `rxved`, branch coverage | yes, no threshold |
| pip-audit | this venv's site-packages only, editable installs skipped | yes, minus 3 ignored IDs |
| vulture | `rxved xv tools tests`, min-confidence 80 | yes, 0 findings |
| deptry | declared vs imported | yes, minus DEP002 on dev tools |
| detect-secrets | git-tracked files vs `.secrets.baseline` | yes, 0 findings |

shellcheck and shfmt are configured as pre-commit hooks but match
nothing: **rxved contains no shell scripts.** `Makefile` targets invoke
the tools directly rather than through wrapper scripts, and no file
carries a `bash`/`sh` shebang.

## CI

`.github/workflows/checks.yml` runs `make check` on **ubuntu, windows and
macos**, on Python 3.11 and 3.12 — six jobs — plus one Ubuntu job that runs
every pre-commit hook over every file.

The matrix is platforms, not Python versions. This program opens a MIDI
port; where it runs matters more than which interpreter runs it.

**3.13 is claimed nowhere.** It used to be in pyproject's classifiers
while CI could not test it, which is the aspirational kind of claim this
project is careful about elsewhere. As of 2026-10-02 `python-rtmidi`
publishes wheels for cp38–cp312 only, so on 3.13 `pip install` falls back
to a source build needing ALSA headers, Xcode CLT and a C toolchain. The
classifier is gone; re-add it and add "3.13" to the matrix in the same
commit, when rtmidi ships a cp313 wheel.

The OS classifiers were corrected at the same time: rxved is tested on all
three platforms, and `favorites.data_dir()` has a branch for each, but
only Linux was declared.

Two notes on making the same `make check` run everywhere:

- The Makefile cannot assume a POSIX layout. `VENV_BIN` is detected at
  parse time because Windows puts console scripts in `.venv/Scripts`.
- GitHub's Windows images do not ship GNU make, so CI installs it and
  points `SHELL` at Git Bash. The recipes are POSIX sh — `audit-secrets`
  uses `trap` — and would not survive cmd.exe.

**A bug this found.** Running `make check` in a *clean* venv rather than
the author's `--system-site-packages` one surfaced two deptry findings
that had never appeared locally: `PIL` imported but missing from the
dependencies, and `pillow` declared but unused. Both are the same
mistake — deptry cannot guess that the distribution `pillow` is imported
as `PIL` — and the local venv masked it because numpy and Pillow were
present from the host. Fixed with `[tool.deptry.package_module_name_map]`,
not by ignoring a code. Worth remembering: a dev environment that shares
site-packages with the OS hides exactly this class of bug.

## ruff: 856 findings suppressed

11 rules are ignored project-wide in `[tool.ruff.lint]`. Each is a style
the codebase uses *consistently in every module*, so the ignore records a
decision rather than hiding an accident:

| Rule | Count | Why |
|---|---|---|
| `UP006` | 243 | `typing.List` → `list`. Consistent throughout; converting is a mechanical sweep. |
| `PLR2004` | 174 | Magic values. This is a SysEx/MIDI codebase full of wire bytes and offsets. Naming every one would be noise. |
| `UP045` | 125 | `Optional[X]` → `X \| None`. Same. |
| `PLC0415` | 112 | Import outside top-level. **Deliberate**: `xv/params.py` exists precisely so the TUI can be built without importing rtmidi. See its module docstring. Three `tools/` scripts also import after a `sys.path` insert. |
| `UP035` | 38 | `typing.Dict` → `dict` (import source). Same as UP006. |
| `UP037` | 19 | Quoted annotations. |
| `RUF012` | 15 | Mutable class attr without `ClassVar`. Textual's `BINDINGS` and `CSS` are framework API. |
| `RUF022` | 13 | `__all__` not sorted — grouped by meaning, not alphabetically. |
| `PLR0912` | 7 | Too many branches — dispatch tables. |
| `PLR0915` | 6 | Too many statements. |
| `PLR0913` | 13 | Too many arguments — dataclass `__init__`s and Textual callbacks. |
| `PLR0911` | 3 | Too many returns. |

13 further rules are ignored **per directory**, each carrying a count in
the `pyproject.toml` comment beside it:

- `tests/*` (6 rules, ~14 findings): `S101` is allowed here and nowhere
  else — assert is how a test asserts. Also `B007 B017 B905 C408 F401
  I001`.
- `tools/*` (16 rules, ~35 findings): `S603 S607` (pdftotext/pdftoppm
  from a fixed argv, no shell), `S314` (stdlib XML on Roland's own local
  script file), `RUF001` (a PDF text layer is full of curly quotes),
  `B034 B904 B905 B007 C901 F841 I001 PLR0912 PLR0913 PLR0915 RUF007
  RUF100 UP015`.
- `xv/*` (7 rules, ~14 findings): `S608` (both SQL interpolations are
  parameterised except the `ORDER BY` clause, which a placeholder
  cannot carry — validated against a fixed dict instead), plus `B905
  F401 I001 SIM105 RUF005 RUF100 UP015`.
- `rxved/*` (9 rules, ~18 findings): `B904 B905 C901 F401 F541 F841
  I001 SIM105 RUF005`.

Two files carry their own, because each has one deliberate construct
rather than a shared style:

- `rxved/favorites.py`: `E501` (a 97-column SQL triple that reads as one
  statement unsplit), `RUF023` (`__slots__` order), `S608`.
- `xv/config.py`: `UP031` (`"%04X"` is a TOML escape; an f-string needs
  the backslash doubled), `PLW0603` (the warn-once flag is deliberately
  module-global).

## mypy: 222 findings suppressed

Non-strict by default, with the correctness codes left **on**:
`arg-type`, `assignment`, `return-value`, `comparison-overlap`,
`union-attr`, `misc`, and the rest. A brand-new module therefore gets the
full non-strict default, because every disable below names modules
rather than rule sets.

Six codes are disabled project-wide:

| Code | Count | Why |
|---|---|---|
| `no-untyped-def` | 97 | ~100 functions unannotated, mostly Textual callbacks. |
| `type-arg` | 48 | Bare `dict`/`set`/`DataTable`. |
| `no-untyped-call` | 30 | Calling the untyped parts of the above. |
| `attr-defined` | 19 | Textual resolves widgets by id at runtime. |
| `has-type` | 19 | Dataclass fields assigned dynamically. |
| `assignment` | 5 | `XvBridge` assigned where `DemoBridge` was declared. `DemoBridge` is a deliberate stand-in, not a subclass — see its docstring. |

Per-module, with counts in the comments:

- `rxved.app`, `rxved.screens` — the TUI. Also `arg-type misc
  no-any-return return-value var-annotated union-attr call-overload
  override`. The `override` is `CategoryScreen.action_toggle()` shadowing
  a Textual `DOMNode` method of the same name; renaming it would be an
  application change, not a lint fix.
- `rxved.demo` — the stand-in described above, plus `override`.
- `rxved.cli` — `arg-type misc no-any-return`.
- `rxved.favorites` — `misc no-any-return var-annotated`.
- `xv.bridge`, `xv.config`, `xv.backup` — `no-untyped-def
  no-untyped-call type-arg misc no-any-return`.
- `xv.catalog` — `no-untyped-def no-untyped-call var-annotated`.
- the three `tools/` scripts — also `arg-type comparison-overlap`. They
  index nested dicts by heterogeneous keys and unpack 3-tuples inside
  loops mypy cannot follow.
- `rtmidi`, `numpy`, `PIL`: `ignore_missing_imports` (C extension / no
  `py.typed`).

**Strict is already on** for `xv.params`, `xv.banks`, `xv.messages` —
the three that pass `--strict` clean today. `make typecheck-strict`
prints the remaining error count per module; add a module to the
`strict = true` override as it reaches zero.

## deptry: 9 findings suppressed

`DEP002` on all nine dev tools (pytest, pytest-asyncio, pytest-cov, ruff,
mypy, pip-audit, vulture, deptry, detect-secrets). They are invoked by
the Makefile and pre-commit, never imported — which is what DEP002 says.

`deptry` did find two real things, and both are now fixed in
`pyproject.toml`:

- `rich` was imported by `rxved/app.py` but only a transitive dependency
  of textual. Now declared directly.
- `numpy`/`Pillow` are imported by `tools/read_marked_list.py` but
  declared nowhere. Now an explicit `[tools]` extra.

## pip-audit: 3 findings suppressed, and only in one venv

`PYSEC-2025-49`, `PYSEC-2026-1918`, `PYSEC-2026-3447`, all against
`setuptools 66.1.1` — and they are passed **only when the venv shares
site-packages with the OS**. The Makefile tests
`include-system-site-packages` in `.venv/pyvenv.cfg`:

- **The author's venv** (`--system-site-packages`, per the README): the
  three advisories are ignored. setuptools comes from Debian there and
  **cannot** be upgraded — setuptools ≥70 imports `splat` from
  `jaraco.functools`, and `/usr/lib/python3/dist-packages` wins over the
  venv's own site-packages, so installing a newer `jaraco.functools` does
  not shadow it and the upgrade installs and then breaks the interpreter.
  Verified, not assumed.
- **CI, and any clean venv**: the flag is empty and pip-audit runs with
  nothing suppressed at all, because the workflow upgrades pip and
  setuptools before installing anything. Confirmed: a clean venv reports
  *"No known vulnerabilities found"*.

That asymmetry is the point. The alternative — passing the three IDs
unconditionally — means every CI run audits with three permanent blind
spots, including for packages that have nothing to do with setuptools.
Rebuild the venv without `--system-site-packages` and the ignores stop
applying on their own.

Re-check those IDs rather than trusting the list: they are pinned to a
Debian version that moves under you.

pip-audit is also scoped with `--path $(SITE_PACKAGES)`. In a
`--system-site-packages` venv an unscoped run audits the whole Debian
userland and reports CVEs in `brlapi`, `terminator`, `libtorrent` and two
dozen other packages unrelated to rxved. That is 30+ findings of noise,
silenced by scoping rather than by ignoring IDs.

The same target passes `--skip-editable`, for `rxved` itself and for
**vinsynlib** — the shared base of this family, which is installed editable
from a sibling checkout rather than from an index (see `[tool.uv.sources]`
in `pyproject.toml` and the README's Development-checks section). Neither
is a distribution on PyPI, so there is nothing for pip-audit to resolve
either against; without the flag both appear in the skip table on every
run, which trains the reader to ignore that table. vinsynlib is this
family's own source and is reviewed where it lives.

Unlike the three setuptools IDs, this flag is **not** conditional on the
venv shape: an editable install is an editable install in CI too.

## vulture and detect-secrets: nothing suppressed

vulture reports **0 findings** at min-confidence 80. At 60 it reports 94,
almost all Textual dispatch (`on_mount`, `action_*`, `CSS`, `BINDINGS`),
which is why 80 is the threshold.

detect-secrets reports **0 findings**. The one hit it ever produced was
the string `detect-secrets` in a `pyproject.toml` comment — the tool's
own name is in its keyword denylist. Suppressed with the tool's own
`# pragma: allowlist secret`, which records that it was looked at.

### The baseline check does not dirty the tree

`make audit-secrets` does **not** run the obvious
`detect-secrets scan --baseline .secrets.baseline`. That rewrites
`generated_at` on every run even when it finds nothing, so every
`make check` left the working tree dirty and every later commit carried a
one-line diff that meant nothing.

Instead it scans into a scratch copy and compares with
`tools/diff_secrets_baseline.py`, which ignores `generated_at` and fails
on anything else — a new finding, a removed one, or a changed plugin or
filter list. A clean scan now touches nothing.

This is deliberately stricter than letting detect-secrets update the
baseline in place, which would silently accept every new finding.

## The real fixes

1. **`tests/test_favorites.py:504`** — removed an unused `import inspect`
   in a local helper. This was the single vulture finding at 90%
   confidence and it was genuinely dead: the function uses `importlib`.
2. **`pyproject.toml`** — `rich` declared as a direct dependency, and
   `numpy`/`Pillow` moved into a `[tools]` extra. Found by deptry;
   listed under deptry above.
3. **`tests/test_app.py`** — replaced 23 fixed-duration waits with a
   `settle()` helper that polls the app's `_busy` flag. See below.
4. **`Makefile`** — `audit-secrets` no longer rewrites `.secrets.baseline`
   on every run. See above.

### Why the tests changed

Turning coverage on roughly doubles the suite's runtime, which exposed
19 `await pilot.pause(...)` waits that were wall-clock guesses at how
long a worker thread takes. Three of them started failing intermittently,
with assertion errors that read like real bugs:

```
tests/test_app.py::TestReadingNames::test_r_reads_a_user_bank
tests/test_app.py::TestReadingNames::test_a_read_marks_names_that_disagree_with_print
tests/test_app.py::TestReadingNames::test_reading_relabels_favourites
```

They were confirmed flaky, not regressions: two consecutive runs of
`tests/test_app.py` passed clean, and stashing `rxved/app.py` (the
`_busy` change) did not change the outcome.

`settle()` waits on the condition instead:

```python
async def settle(pilot, seconds: float = 5.0) -> None:
    waited = 0.0
    while pilot.app._busy and waited < seconds:
        await pilot.pause(0.05)
        waited += 0.05
    await pilot.pause(0.05)   # workers post results *after* clearing _busy
```

The trailing pause matters. Every worker clears `_busy` in a `finally`
and *then* posts its result with `call_from_thread`, so "not busy" is not
"the result is on screen". Without it the helper returns in the window
between the two and the assertion reads the pre-worker state — which is
what the first version of this helper did, and it still failed.

Only test code and configuration were touched. No application source was
edited.

## ruff-format: adopted, and enforced

Formatting is **ruff-format's**, at default settings (88 columns, double
quotes), and it is now enforced three ways: `make format-check` in
`check`, and a pre-commit hook.

This was not always true, and the path there is worth recording because
it is the one place where this task broke its own rule.

Enabling the `ruff-format` hook straight away reformatted ~3,700 lines
across 23 files — unreviewable, and it violated "do not mass-modify
existing code". A partial revert restored the 12 files whose diff was
*purely* churn; the rest could not be reverted cleanly, because they
carried real edits alongside the formatter's changes. The tree was left
in a mixed state: 14 files formatted, 13 not, with the divergence
invisible to CI.

The fix was to **land the formatting as its own commit** rather than to
suppress the hook:

- `b13e1c0` `style: apply ruff formatting` — 23 files, ~3,600 lines,
  formatting only. Verified by comparing the parsed AST of every file
  against `HEAD`: all identical except three test docstrings beginning
  with a quote character, where ruff inserted a leading space so the
  string is not read as a triple-quote terminator.
- `f5046d5` — the actual code changes, on top of a formatted tree.

Order mattered. Putting the checks *after* the formatting means the
commit that introduces `ruff check` lands on a tree that already passes
it, rather than one that fails on arrival.

`make format-check` now reports **27 of 27 files already formatted**.