# Audit of `my-snaps`

**Audit date:** 2026-10-24  
**Last verified against the working tree:** 2026-10-24  
**Scope:** the working tree in this repository (not only the published PyPI artifact).  
**Method:** source review of every module, the `pytest` suite in [`tests/`](tests), and the targeted non-destructive reproductions from the first pass (monkeypatched subprocesses and a temporary `rsync` directory). No `btrfs` commands were run and no mounts were touched.

---

## Bottom line

Keep it as a **personal, opinionated tool**. No correctness or safety defect remains open, and no code path can silently report a failed `btrfs` command as success. What remains is release hygiene: a 2.0.0 tag and a changelog.

---

## What the project is, in evidence

- 2,751 lines across five modules ([`main.py`](my_snaps/main.py) 1,477; [`my_restore.py`](my_snaps/my_restore.py) 521; [`InlineMenu.py`](my_snaps/InlineMenu.py) 437; [`bt_smart_balance.py`](my_snaps/bt_smart_balance.py) 208; [`MyUtils.py`](my_snaps/MyUtils.py) 108), one runtime dependency, MIT.
- A `pytest` suite in [`tests/`](tests) and one GitHub Actions workflow ([`.github/workflows/ci.yml`](.github/workflows/ci.yml#L1)) that runs Ruff and Pylint plus `pytest` with coverage on Python 3.11–3.13.
- No git tags and no changelog. Bug and feature issue forms live in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE), with security reports routed to GitHub's private advisory form and questions to GitHub Discussions. A `SECURITY.md` file and a man page are intentionally out of scope.
- [`pyproject.toml`](pyproject.toml#L9) declares 2.0.0, `requires-python = ">=3.11"`, and a dependency range of `console-window >= 2.0.0.10, <3`; nothing in the repository ties that version to a released commit.
- Three console scripts: `my-snaps`, `my-restore`, `bt-smart-balance`.

---

## Remaining work

- **Release anchor.** Git tags and a changelog are still missing; the repository has no anchor for the 2.0.0 version in [`pyproject.toml`](pyproject.toml#L9).
- **Debug harness.** `runner` in [`InlineMenu.py`](my_snaps/InlineMenu.py#L370) is a debug convenience, not a test harness.

---

## Keep or not

**Keep it if:**
- The primary use matches the assumptions (mounted `/.snapshots`, no dots in top-level names, labeled filesystems).
- The simple model and the systemd-boot/pacman/dracut workaround are worth more than a generalized tool.
- Finishing the release tag and changelog is acceptable.

**Do not keep it as a public/general-purpose project unless taking on:**
- Portability across unlabeled BTRFS and non-systemd-boot setups.
- Ongoing tests, CI, tags, changelog, docs, and support.
- The trust bar for a root tool that deletes subvolumes.

Tool alternatives and the discriminating features are covered in [`README.md`](README.md#how-it-compares).
