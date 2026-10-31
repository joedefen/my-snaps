# Adopting `bless` in `my-snaps`

**Status:** plan only. [`bless`](bless) is present and executable, copied
verbatim, but the project does not yet satisfy its assumptions. Until the steps
below are done, `./bless` refuses at start-up because
[`my_snaps/__init__.py`](my_snaps/__init__.py) has no `__version__` line.

## Source and scope

[`bless`](bless) was copied byte-for-byte from
`../pwr-tray/bless` (SHA-256
`4f756337840b0619fbf6f077b00d4bb3fb9aee95ff18bbeb6a0521d39d52131e`),
which is also the current file in `../vappman` before the colour additions.
The file is deliberately **not edited here**: reuse is "copy verbatim", and
project identity is read from [`pyproject.toml`](pyproject.toml) and
[`my_snaps/__init__.py`](my_snaps/__init__.py). The later colour work will be
adopted as a new upstream copy, not as local edits.

No `[tool.bless]` table is required once the defaults line up:

| `identity()` default | `my-snaps` value |
| --- | --- |
| `[project] name` | `my-snaps` |
| module (name with `-`/`.` → `_`) | `my_snaps` |
| first `[project.scripts]` entry | `my-snaps` |
| inspector | `tests/inspect_dist.py` |

## Compliance matrix

| Requirement in [`bless`](bless:58) | Current tree | Action |
| --- | --- | --- |
| Flat layout: package directory at the root | `my_snaps/` is at the root | None |
| `__version__ = "X.Y.Z"` in `my_snaps/__init__.py` | File is empty | Add the version line |
| Build backend reads that version | `pyproject.toml` has static `version = "2.0.1"` | Replace with `dynamic = ["version"]` |
| Version is not already published | PyPI has `2.0.1` (and `1.0.0`–`2.0.0`) | Bump to `2.0.2` in `__init__.py` |
| First console script prints the version for `--version` | `my-snaps = my_snaps.main:run`; `main()` has `--version` | None (smoke venv installs the metadata `version_string()` reads) |
| `python -m build` works | flit_core 3.12 is installed; `build` is installed | Add `build>=1.2` to `dev` so the requirement is recorded |
| Dev extra holds `ruff`, `pytest`, `pytest-cov`, `build`, `twine>=7`, `packaging>=26` | Has ruff/pytest/pytest-cov; missing the other three; host has twine 6.1.0 and packaging 25.0 | Add all three and reinstall `.[dev]` |
| Inspector at `tests/inspect_dist.py` | Missing | Add an adapted copy of vappman's inspector |
| Clean working tree | Tracked tree is clean; `build/` and `.ruff_cache/` are not in `.gitignore` | Add gate artifacts to `.gitignore` |
| `vX.Y.Z` tag absent | No tags exist | None |
| `main` and `dev` both exist locally and as `origin/*` | Only `main` exists | Create `dev`, push it |
| Credentials per index | None checked in | Keep `TWINE_PASSWORD` / `TWINE_TESTPYPI_PASSWORD` or matching `~/.pypirc` sections out of the tree |

## Ordered implementation plan

### 1. Move the version to the one place `bless` reads

- Add `__version__ = "2.0.2"` to
  [`my_snaps/__init__.py`](my_snaps/__init__.py). `2.0.1` is already on PyPI,
  so the duplicate check would refuse it; `2.0.2` is the next free patch.
- In [`pyproject.toml`](pyproject.toml:9), delete `version = "2.0.1"` and add
  `dynamic = ["version"]`. flit_core reads the line above.
- Do not put the version anywhere else. The rehearsal rewrites only the copy's
  `__init__.py`, never the working tree.
- Keep `version_string()` as the runtime reader of installed metadata; the
  smoke check runs it from the wheel installed in a throwaway venv, where it
  reports the built version. (Optional cleanup, not required by `bless`: have
  it fall back to `my_snaps.__version__` in a source tree.)

### 2. Add the distribution inspector

- Create `tests/inspect_dist.py` adapted from `../vappman/tests/inspect_dist.py`
  for `my-snaps` / `my_snaps`.
- It must be importable both by `./bless` (`check_all(wheel, sdist, version)`)
  and by CI (`python tests/inspect_dist.py <wheel> <sdist>`).
- Keep reading expectations from [`pyproject.toml`](pyproject.toml) and the
  package, never from the artifact under test.
- Assertions to carry over:
  - wheel: `Name`, `Version` (or the rehearsal override), `Summary`,
    `Requires-Python`, `Keywords`, `License-Expression`, `License-File`, and
    runtime `Requires-Dist` names match the tree;
  - wheel: every `my_snaps/*.py` is present, `LICENSE` is carried, no
    `tests/`, no `.venv`, no `__pycache__`, no `.pyc`;
  - sdist: `pyproject.toml`, `README.md`, `LICENSE`,
    `my_snaps/__init__.py`, `my_snaps/main.py` are present, the embedded
    `__version__` equals the artifact version, and no venv or bytecode is
    included.

### 3. Add the rest of the release toolchain to `dev`

In [`pyproject.toml`](pyproject.toml:32):

```toml
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "pylint>=3.3",
    "ruff>=0.9",
    "build>=1.2",
    "twine>=7",
    "packaging>=26",
]
```

Then install into the interpreter that will run `./bless`:

```sh
python -m pip install -e '.[dev]'
```

The host currently has `twine 6.1.0` and `packaging 25.0`; `bless` refuses
floors below 7 and 26 because it sends the two named files it built and runs
`twine check --strict` on Metadata 2.5 from flit_core 3.12.

### 4. Keep gate artifacts out of `git status`

`bless` refuses a dirty tree before it does anything. Extend
[`.gitignore`](.gitignore) with:

```gitignore
build/
.ruff_cache/
*.whl
*.tar.gz
.coverage.*
coverage.xml
```

`dist/`, `.coverage`, `.pytest_cache/`, `.mypy_cache/`, `htmlcov/`, `.venv/`
and `__pycache__/` are already ignored. `images/` is only 204 KB, so the
rehearsal-copy exclusion issue noted in vappman does not need special handling.

### 5. Create the branch pair

`bless` publishes only from `main` or `dev`, and refuses unless both branches
and both remote refs exist:

```sh
git branch dev
git push -u origin dev
```

Keep `main` and `dev` at one commit before publishing; `publish` fast-forwards
the other branch and tags the commit.

### 6. Align CI and the developer documentation

- Add a packaging job to [`.github/workflows/ci.yml`](.github/workflows/ci.yml)
  that runs the same commands as the gate, not equivalents:
  `ruff check .`, `compileall -q my_snaps tests`, `pytest -q --cov=my_snaps
  --cov-report=term-missing`, then `python -m build`, the shared
  `tests/inspect_dist.py` on the pair, `twine check --strict`, and a
  smoke install of the wheel. Keep it credential-free.
- Keep the existing Pylint and coverage-floor steps; `bless` is a floor, not a
  replacement for the stronger CI gate.
- Run CI on `dev` as well as `main`.
- Add `README-DEV.md` (modeled on vappman's) covering: the one version source,
  the gate stages, the `test`/`publish` prompt, TestPyPI rehearsal and its
  install line (`pip cache purge`, `--extra-index-url`), the per-index
  credentials, the patient index verification, and the release steps.
- Update the development section of [`README.md`](README.md:146) to name
  `./bless` and the `dev` extra.

### 7. Add regression tests for the adoption

Recommended before the first real release, because the current CI enforces an
80% coverage floor and the gate is otherwise untested here:

- Port the pure parts of `../pwr-tray/tests/test_bless.py`: identity derivation,
  preflight refusals, duplicate check before any build, exact
  `test`/`publish`/`quit` prompt handling, `X.Y.Z.99.N` arithmetic, and the
  verifier's lag handling.
- Add the invariant checks from `../pwr-tray/tests/test_repo_hygiene.py`:
  [`bless`](bless) exists and is executable, the docs name it, no documented
  command uploads `dist/*`, and CI and `bless` share `tests/inspect_dist.py`.
- Add a test that [`pyproject.toml`](pyproject.toml) declares
  `dynamic = ["version"]` and that `my_snaps.__version__` is the only version
  literal, so a future edit cannot silently bring back a static mismatch.

### 8. First run, rehearsal, release

1. Commit all of the above; confirm `git status --porcelain` is empty.
2. Run `./bless`. It must reach the checklist and then ask
   `====> Enter test or publish or quit:`.
3. Answer `test` first. This builds `2.0.2.99.N` from a throwaway copy and
   uploads only to TestPyPI; it creates no tag, moves no branch and spends no
   PyPI token.
4. Run `./bless` again and answer `publish` to tag `v2.0.2`, upload to PyPI,
   verify the index, and push `main`, `dev` and the tag.

## Acceptance criteria

- `./bless` passes identity, preflight, the index check, all three gates, build,
  inspection, `twine check --strict`, smoke install and the branch plan, then
  stops at the prompt with nothing changed.
- Answering anything other than `test`, `publish` or `quit` asks again; Enter,
  `quit` or Ctrl-C leaves the tree, tags and index untouched.
- Answering `test` touches only TestPyPI and `build/`.
- Answering `publish` leaves `main` and `dev` at the tagged commit and the
  index serving the same wheel SHA-256 that was inspected.
- CI runs the same `tests/inspect_dist.py` that `bless` uses.

## Deferred

- The colour additions to `bless` were made after the vappman copy; they will
  be adopted by replacing this file with the updated upstream copy, not by
  editing [`bless`](bless) locally.
- `bless` requires Python 3.11+ (`tomllib`); `my-snaps` also requires 3.11+, so
  the vappman interpreter caveat does not apply here.
- Coverage is already enforced by CI (`--cov-fail-under=80`); whether to mirror
  that floor in `[tool.coverage.report]` is independent of `bless`.
