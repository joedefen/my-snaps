# my-snaps: working on the source tree

This is the developer companion to [`README.md`](README.md), which documents the
tools. Here we document the two things a maintainer needs and a user does not:
the **test gate** and the **release gate** (`./bless`).

## Layout and single sources of truth

| Path | Owns |
| --- | --- |
| [`my_snaps/__init__.py`](my_snaps/__init__.py) | `__version__`, and nothing else. The build backend reads it, `--version` prints it via installed metadata, `bless` reads the line without importing. |
| [`pyproject.toml`](pyproject.toml) | Name, description, keywords, `requires-python`, runtime dependencies, the SPDX `license` and `license-files`, the `dev` extra, pytest, ruff and pylint configuration. |
| [`bless`](bless) | The release gate. Emitted from `~/Projects/blessd/bless` by that repository's `build.py`; it holds no copy of this project's identity and is never edited here. |
| [`tests/inspect_dist.py`](tests/inspect_dist.py) | The distribution inspector. `bless` refuses to upload a wheel and sdist it has not inspected with this file. |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | CI: lint (ruff, pylint), tests with the coverage floor on Python 3.11 -- 3.13, and a packaging job that builds, inspects and smoke-installs the wheel. |
| [`.gitignore`](.gitignore) | Keeps gate artifacts (`build/`, `dist/`, `.coverage*`, caches, `*.whl`, `*.tar.gz`) out of `git status`, because the gate refuses a dirty tree. |
| `~/Projects/blessd/docs/release-strategy.md` | The shared release policy behind the gate: branch model, index verification, and the per-index credentials. Read there; it is not restated here. |

The version lives in exactly one place. `pyproject.toml` declares
`dynamic = ["version"]`, so flit reads `my_snaps/__init__.py`; a `X.Y.Z.99.N`
rehearsal rewrites that line only inside a throwaway copy.

No `[tool.bless]` table is needed: everything is derived from what is already
there -- `NAME = my-snaps`, `MODULE = my_snaps`, the console script from the
first `[project.scripts]` entry (`my-snaps`), and the inspector at
`tests/inspect_dist.py`. The branch model is the default `["main", "dev"]`;
this project has both branches, so the default is the correct declaration.

## Adoption note

The vendored gate is byte-for-byte the build emitted by
`~/Projects/blessd/build.py`. This project carries sha256
`3ef60e9e806ca58a3d900dd6622bb8be0052210da1ef26f9918bf0a1deb1e3b2`; reproduce
it from the project root with:

    python3 ~/Projects/blessd/build.py --into . --force
    cmp ~/Projects/blessd/bless bless

Never edit [`bless`](bless) here: the copy is what this project releases with,
and a local edit would make `bt audit` report it `stale`. Re-emit from `blessd`
instead.

To satisfy the gate, the project itself supplies the single version location
([`my_snaps/__init__.py`](my_snaps/__init__.py)), the console scripts in
`[project.scripts]` whose first entry reports `--version`, the inspector at
[`tests/inspect_dist.py`](tests/inspect_dist.py), and the `dev` extra holding
`build`, `twine>=7` and `packaging>=26`. The gate's own policy and its tests
live in `blessd`; [`tests/test_bless.py`](tests/test_bless.py) keeps only this
project's identity and inspector assertions.

## The test gate

Install what the gate needs (this is the interpreter that must run `bless`,
too):

    python -m pip install -e '.[dev]'

Then the commands `bless` runs, in the same order, on the same interpreter:

    python -m ruff check .
    python -m compileall -q my_snaps tests
    python -m pytest -q --cov=my_snaps --cov-report=term-missing

Today that is 392 tests at 94% coverage. The coverage floor (80%) is enforced by
CI's `--cov-fail-under=80`; `bless` reports coverage but does not enforce it.

The ruff selection is committed rather than inherited
(`[tool.ruff.lint] select = ["E4", "E7", "E9", "F", "I", "UP", "B"]`) so that no
user or global configuration can change what the gate enforces.

CI additionally runs `python -m pylint my_snaps`, which is *not* part of the
bless gate: the gate is the release requirement, and pylint is the stricter
house-style check CI keeps an eye on.

## The release gate: `./bless`

`./bless` takes no arguments and has no flag that skips a stage. It always runs
everything, then prints what it has in hand and asks one question:

    ====> Enter test or publish or quit:

Only `test` and `publish` are accepted, spelled in full; anything else is asked
again. Enter, `quit` or Ctrl-C stops with nothing tagged, uploaded or pushed.

**Stages, in order**

1. **preflight** -- clean tree, the `vX.Y.Z` tag absent, `twine>=7` and
   `packaging>=26` present, and the trunk (`main`) local and on `origin`. A
   `dev` that does not exist yet is tolerated; once it exists it must be on
   `origin` too.
2. **index check** -- refuses if `__version__` is already on PyPI, printing
   every published version, before anything is built or uploaded.
3. **gates** -- the three commands above.
4. **build** -- into `build/bless-<version>/`, wiped first, so no older
   artifact can be sent by mistake.
5. **inspect** -- `tests/inspect_dist.py` against the wheel and the sdist.
6. **check** -- `twine check --strict` on both files.
7. **smoke** -- installs the built wheel into a throwaway venv and runs
   `my-snaps --version`, which must print the version being released. This is
   why every console script has `--version`
   ([`parse_args()`](my_snaps/main.py:1394)).
8. **branch plan** -- reads `[tool.bless] branches` (here `main` and `dev`),
   refuses a detached HEAD, a branch outside that model, diverged branches, or
   a push that would not fast-forward. `./bless --branch-plan` prints the same
   plan as JSON without running the gate.

**`test`** is the rehearsal and never spends the working version. It copies the
tree to `build/bless-test-<version>/src`, rewrites `__version__` to
`X.Y.Z.99.N` (99 keeps it clear of real releases; `N` is one past the highest
suffix TestPyPI already holds for that base), builds, inspects and smoke-tests
the copy, and uploads only to `test.pypi.org`. No tag, no branch, no push. It
prints the install line, which needs `python -m pip cache purge` first and
`--extra-index-url https://pypi.org/simple/`, because TestPyPI carries no
dependencies.

**`publish`** aligns the branches named by `[tool.bless] branches` -- `main` and
`dev` here -- at one commit, tags `vX.Y.Z`, uploads the two inspected files to
PyPI, verifies the index, and pushes those branches and the tag.

**Credentials** are resolved per index -- `TWINE_PASSWORD` for PyPI,
`TWINE_TESTPYPI_PASSWORD` for TestPyPI, each overruling the `~/.pypirc` section
whose `repository` matches that index's upload URL -- so a PyPI token is never
spent on a rehearsal. A missing credential is a refusal before `twine` starts.

**Verification** reads the index back after an upload. The read is cache-busted
and patient (up to a quarter of an hour), because both indexes list new releases
through a CDN that lags; a release that is not listed yet is reported as lag
with the project page to check, never as a failed upload, since an accepted
upload cannot be withdrawn.

There is no CI publish job, on purpose: the upload is irreversible and is run by
the owner from the development tree, so the safety lives in one inspectable
command. CI stays a gate.

No interpreter caveat applies here: `my-snaps` already requires Python 3.11+,
which is also what `bless` needs for `tomllib`.

## What the inspector asserts

[`tests/inspect_dist.py`](tests/inspect_dist.py) is the one implementation, used
by `bless` and by the CI packaging job; expectations are read from
`pyproject.toml` and the package, never from the artifact under test.

* **wheel** -- `Name`, `Version`, `Summary`, `Requires-Python`, `Keywords`,
  `License-Expression` and the *runtime* `Requires-Dist` names match
  `pyproject.toml`; every declared `license-files` entry is listed in
  `License-File`; every `my_snaps/*.py` is present; a `LICENSE` is carried; no
  `tests/`, no `.venv`, no `__pycache__`, no `.pyc`.
* **sdist** -- carries `pyproject.toml`, `README.md`, `LICENSE`,
  `my_snaps/__init__.py`, `my_snaps/main.py`; the `__version__` inside the sdist
  equals the artifact version; no venv or bytecode. The sdist is deliberately
  lean: flit's default payload is enough and `tests/` need not ride along.

Run it by hand on any built pair:

    python tests/inspect_dist.py dist/my_snaps-2.0.2-py3-none-any.whl \
                                dist/my_snaps-2.0.2.tar.gz

## Releasing, in three steps

1. Bump `__version__` in [`my_snaps/__init__.py`](my_snaps/__init__.py) and
   commit.
2. Run `./bless`. The gate runs in full.
3. Answer `test` to rehearse on TestPyPI, then run `./bless` again and answer
   `publish` -- or go straight to `publish`.

`main` and `dev` are this project's branch model; `publish` leaves them at the
tagged commit. Everything else about the release is one of the stages above, and
the shared policy behind it is
`~/Projects/blessd/docs/release-strategy.md`.

Re-adopt before a release if the gate has moved: `bt audit` reports this
project's copy as `current` or `behind`, and `bt status` prints
`blessd status: ...` whenever it is not current.

## FUTURE ENHANCEMENTS

1. **Enforce the coverage floor in the gate.** CI passes
   `--cov-fail-under=80`; moving that into `[tool.coverage.report]` would make
   `bless` and CI enforce the identical number.
2. **Apply the per-project environment model.** `bt penv` would make `.venv`
   the editable/debug environment and `bt edit` would point the entry point at
   the tree; see `~/Projects/blessd/docs/adopting.md` for the migration.
