# Audit of `my-snaps`

**Audit date:** 2026-10-24  
**Last verified against the working tree:** 2026-10-24  
**Scope:** the working tree in this repository (not only the published PyPI artifact).  
**Method:** source review of every module, the `pytest` suite in [`tests/`](tests), and the targeted non-destructive reproductions from the first pass (monkeypatched subprocesses and a temporary `rsync` directory). No `btrfs` commands were run and no mounts were touched.

---

## Bottom line

Keep it as a **personal, opinionated tool**. The last correctness item is fixed: a multi-device BTRFS filesystem is now discovered, mounted and listed once per filesystem by grouping the scan on its filesystem UUID. What remains is packaging and project hygiene. No open item can silently report a failed `btrfs` command as success.

---

## What the project is, in evidence

- 2,751 lines across five modules ([`main.py`](my_snaps/main.py) 1,477; [`my_restore.py`](my_snaps/my_restore.py) 521; [`InlineMenu.py`](my_snaps/InlineMenu.py) 437; [`bt_smart_balance.py`](my_snaps/bt_smart_balance.py) 208; [`MyUtils.py`](my_snaps/MyUtils.py) 108), one runtime dependency, MIT.
- A `pytest` suite in [`tests/`](tests) and one GitHub Actions workflow ([`.github/workflows/ci.yml`](.github/workflows/ci.yml#L1)) that runs Ruff and Pylint plus `pytest` with coverage on Python 3.11–3.13.
- No git tags and no changelog. Bug and feature issue forms live in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE), with security reports routed to GitHub's private advisory form and questions to GitHub Discussions. A `SECURITY.md` file and a man page are intentionally out of scope.
- [`pyproject.toml`](pyproject.toml#L9) declares 2.0.0, `requires-python = ">=3.11"`, and a dependency range of `console-window >= 2.0.0.10, <3`; nothing in the repository ties that version to a released commit.
- Three console scripts: `my-snaps`, `my-restore`, `bt-smart-balance`.

---

## What is genuinely worth keeping

1. **The mental model is excellent.** Labels (`=Update`, `=Daily`) plus “replace the eldest of the same label” is a clearer description of the pre-update use case than snapper’s timeline terminology. The naming convention is transparent and greppable.
2. **Tiny maintenance surface.** No daemon, no DBus, no systemd unit, no database, one dependency. The entire thing can be read in an evening.
3. **The restore theory is simple and reversible.** Rename the current subvolume to `=Reverted`, create a writable subvolume from the read-only snapshot, reboot. That is much easier to reason about than a bootloader-integrated rollback.
4. **The `.efi-back` workaround is a real differentiator** for your systemd-boot + pacman + dracut setup, documented in [`README.md`](README.md#L155). Snapper, Timeshift, and btrbk do not solve that out of the box.
5. **Both interaction modes are useful.** The curses TUI for “before I update,” and `-p`/`-s`/`--cron` for automation, with no config file.
6. **The regression suite is real.** The tests run without a BTRFS filesystem, a mount, or root by substituting fake `btrfs`/`lsblk`/`rsync` executables, which is what makes the remaining fixes cheap.
7. **It is yours.** That is a legitimate reason to keep it. A 2-star personal tool that fits your workflow is not a failure; it is a tool.

---

## Defects found

### P1 — correctness, safety, and portability

1. **Multi-device BTRFS filesystems appear as several devices. — FIXED 2026-10-24.** [`_load_devs()`](my_snaps/main.py#L868) now groups scan records by filesystem UUID, so one filesystem has one `devs` entry, one read-only mount, one writable mount and one `df` header. Member names resolve to that entry through `dev_aliases`, and the device column shows the first member plus the number of additional members (e.g., `sda1+1`). Covered by [`tests/test_device_scan.py`](tests/test_device_scan.py#L185).

### P2 — maintenance and discoverability

- ~~[`.github/workflows/ci.yml`](.github/workflows/ci.yml#L1) ran `pylint` and `pytest` on Python 3.11 alone, with no ruff step.~~ **Done 2026-10-24:** CI now runs `ruff check`, `pylint`, and `pytest` with `--cov=my_snaps --cov-fail-under=80` on Python 3.11, 3.12, and 3.13. The suite covers 95% of statements.
- ~~No issue templates.~~ **Done 2026-10-24:** bug and feature forms are in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE); the chooser links security reports to GitHub's private advisory form and questions to GitHub Discussions. `SECURITY.md` and a man page are deliberately not planned.
- Git tags and a changelog are still missing; the repository has no anchor for the 2.0.0 version in [`pyproject.toml`](pyproject.toml#L9).
- ~~The GitHub description (“Basic upgrade and BTRFS tools for Fedora and EOS”) no longer matches the PyPI summary (“Simple BTRFS Snapshot Tools”).~~ **Done:** the repository description now matches the PyPI summary.
- No tag or changelog anchors the 2.0.0 version in [`pyproject.toml`](pyproject.toml#L9); `runner` is a debug convenience, not a test harness.
- ~~License metadata is still the old `license = { text = "MIT" }` form ([`pyproject.toml`](pyproject.toml#L15)); it works, but modern PEP 639 metadata is cleaner.~~ **Done 2026-10-24:** [`pyproject.toml`](pyproject.toml#L15) now uses the PEP 639 SPDX expression `license = "MIT"` plus `license-files = ["LICENSE"]`, and drops the deprecated `License ::` classifier. `flit_core>=3.12` in [`pyproject.toml`](pyproject.toml#L4) is required for those keys. Verified by building both artifacts: the wheel and sdist carry `Metadata-Version: 2.4`, `License-Expression: MIT`, `License-File: LICENSE`, and bundle the license at `*.dist-info/licenses/LICENSE`.

---

## How it compares

Popularity figures are as of 2026-10-24.

| Tool | Snapshot model | Strengths | Cost / gap | Fit for this use case |
|---|---|---|---|---|
| **my-snaps** | label + replace-eldest, TUI, rename-based restore | Tiny, no daemon, transparent names, tailored `.efi-back` workaround, regression suite | Packaging/docs still unfinished, one maintainer | **Keep personal** |
| **snapper** (+ snap-pac, grub-btrfs) | timeline, config retention, rollback | Mature, distro integration, boot-menu rollback (~1.2k stars) | Config/DBus/units, more moving parts | Best if you want point-and-click rollback |
| **btrbk** | snapshot + send/receive + retention | Proven offsite backups, flexible retention (~2.2k stars) | Config-heavy, no TUI restore | Use for backups, not daily updates |
| **btrfsmaintenance** | balance/scrub/trim jobs | Maintained, correct, systemd/cron integration (~1.1k stars) | Not a snapshot tool | Complement `bt-smart-balance` |
| **Timeshift** | scheduled snapshots, GUI restore | Easy GUI, popular (~4.3k stars on the Mint fork) | Heavy, less CLI-friendly, less active | Use if you want a GUI |
| **snap-pac** | pacman pre/post hooks | Automatic pre-update snapshots (~231 stars) | Arch-only, needs snapper | Complements my-snaps |
| **yabsnap** | config-driven Arch snapshots | Simple, pacman hooks, close in spirit (~113 stars) | Config file, less interactive | Closest philosophical peer |
| **grub-btrfs** | boot-menu snapshot entries | Rollback from the bootloader (~1.2k stars) | Requires snapper/snapshots | Add only if you want boot rollback |
| **btrfs-assistant** | GUI for snapper/btrbk | Convenient install/rollback | Heavier GUI stack | Not needed for this workflow |

The closest peers are **yabsnap** and **snap-pac**, not snapper or btrbk. `my-snaps`’ niche is “pre-update snapshot plus conservative manual restore.” It is not, and should not try to be, a retention-policy engine or a backup tool.

---

## Keep or not

**Keep it if:**
- You are the primary user and your layout matches the assumptions (mounted `/.snapshots`, no dots in top-level names, labeled filesystems).
- You value the simple model and the systemd-boot/pacman/dracut workaround.
- You are willing to finish the remaining P2 packaging and project-hygiene work.

**Do not keep it as a public/general-purpose project unless you want to take on:**
- Portability across unlabeled and multi-device BTRFS, and non-systemd-boot setups.
- Tests, CI (ruff plus a wider Python matrix), tags, changelog, docs, and support.
- The trust bar for a root tool that deletes subvolumes.

**If you want less risk with less maintenance:**
- Keep `my-snaps` for snapshots and `my-restore` for restores; the P1 device-grouping fix is already in place.
- Keep `bt-smart-balance`: it launches a bounded `-dusage` background balance and refuses to start a second one. Use **btrfsmaintenance** instead if you want a maintained job runner.
- Use **btrbk** for offsite/retention and **snapper + grub-btrfs/snap-pac** only if you want boot-menu rollback.
- Add a short “personal, unsupported, Linux-only, BTRFS-only” note to the README. It sets expectations and is honest about the project’s scope.

---

## Minimum fix list, in order

1. ~~**Deduplicate multi-device filesystems** in [`_load_devs()`](my_snaps/main.py#L854), keying devices by filesystem UUID so a filesystem is mounted and counted once rather than once per member.~~ **Done:** [`_load_devs()`](my_snaps/main.py#L868) groups by UUID and aliases every member name; [`tests/test_device_scan.py`](tests/test_device_scan.py#L185) asserts a two-member filesystem produces one entry and one `df` call.
2. ~~**Extend CI:** add a ruff step and run the matrix on 3.11–3.13.~~ **Done:** CI runs Ruff, Pylint, and coverage-gated tests on all three interpreters. Tagging 2.0.0 and adding a CHANGELOG remain.
3. ~~**Add project hygiene:** issue templates.~~ **Done:** bug/feature forms and the security/discussion contact links are in [`.github/ISSUE_TEMPLATE/`](.github/ISSUE_TEMPLATE). `SECURITY.md` and a man page are intentionally not planned.

The remaining work is a release tag and a changelog. Items 1–3 are implemented and covered by tests; the suite reports 95% statement coverage.
