# Audit of `my-snaps`

**Audit date:** 2026-10-24  
**Scope:** the working tree in this repository (not only the published PyPI artifact).  
**Method:** source review, compile check of all modules, and targeted non-destructive reproductions with monkeypatched subprocesses and a temporary `rsync` directory. No `btrfs` commands were run and no mounts were touched.

---

## Bottom line

Keep it, but keep it as a **personal, opinionated tool** for now. The design is genuinely good: small, legible, no daemon, no config language, and a snapshot naming scheme you can understand by looking at `/.snapshots`. The implementation, however, has a silent-failure bug in the one command wrapper that every snapshot and deletion depends on, plus several defects in the restore path. Those are fixable in a day or two, but they are the reason it should not be recommended to strangers yet.

---

## What the project is, in evidence

- 1,875 lines across five modules, one runtime dependency, MIT.
- 9 PyPI releases; latest published is 1.0.9 from 2025-10-17.
- GitHub: 2 stars, 0 forks, 0 open issues, last push 2026-10-24.
- No tests, no CI, no git tags, no changelog, no release artifacts on GitHub.
- The working tree is **ahead of PyPI**: local [`pyproject.toml`](pyproject.toml#L7) still says 1.0.9 but pins [`console-window == 2.0.0.10`](pyproject.toml#L16), a version published 2026-10-24 13:23 (2.0.0.10 adds the inline-editor/caret support `main.py` now relies on). The published 1.0.9 wheel requires `console-window >= 1.0.0`. So the checkout is an unreleased 1.0.9+ and the version must be bumped before another upload.

That last point matters for the audit: the bugs below are in the code you are running from source, which may differ from what PyPI users have.

---

## What is genuinely worth keeping

1. **The mental model is excellent.** Labels (`=Update`, `=Daily`) plus “replace the eldest of the same label” is a clearer description of the pre-update use case than snapper’s timeline terminology. The naming convention is transparent and greppable.
2. **Tiny maintenance surface.** No daemon, no DBus, no systemd unit, no database, one dependency. The entire thing can be read in an evening.
3. **The restore theory is simple and reversible.** Rename the current subvolume to `=Reverted`, create a writable subvolume from the read-only snapshot, reboot. That is much easier to reason about than a bootloader-integrated rollback.
4. **The `.efi-back` workaround is a real differentiator** for your systemd-boot + pacman + dracut setup, documented in [`README.md`](README.md#L144). Snapper, Timeshift, and btrbk do not solve that out of the box.
5. **Both interaction modes are useful.** The curses TUI for “before I update,” and `-p`/`-s`/`--cron` for automation, with no config file.
6. **It is yours.** That is a legitimate reason to keep it. A 2-star personal tool that fits your workflow is not a failure; it is a tool.

---

## Defects found

> **Status as of 2026-10-24:** ✅ = fixed in the working tree and covered by tests; ⏳ = partially done; unmarked = still open. Fixed: defects 1–9 and 11, and minimum-fix items 1–8 and 11. Still open: minimum-fix items 9–10 and 12 (⏳), plus the P1 items other than 7–9 and 11.

### P0 — can silently break the core promise (all fixed ✅)

1. ✅ **`_slurp_command()` reports every failed `btrfs` command as success.**
   [`_slurp_command()`](my_snaps/main.py#L355) calls `process.wait()` and then applies [`os.WEXITSTATUS(status)`](my_snaps/main.py#L363). `Popen.wait()` already returns an exit code (0–255), so `os.WEXITSTATUS(1) == 0`. I verified with `true`, `false`, and `sh -c "exit 42"`: all three returned code `0`.
   Consequences: [`_create_snap()`](my_snaps/main.py#L293) treats a failed snapshot as success and marks the view dirty; [`_del_subvolume()`](my_snaps/main.py#L329) treats a failed deletion as success, removes the entry from the in-memory list, and lets retention drift. That is the worst possible failure mode for a pre-update snapshot tool.
   Fix: use `process.communicate()` once and return `process.returncode` directly.

2. ✅ **`bt-smart-balance` never actually balances.**
   [`balance_filesystem()`](my_snaps/bt_smart_balance.py#L55) builds the command with [`self.opt`](my_snaps/bt_smart_balance.py#L58), which does not exist; I reproduced `AttributeError: 'BtSmartBalance' object has no attribute 'opt'`. Even after correcting that, the next line overwrites `cmd` with only [` --bg {mount}`](my_snaps/bt_smart_balance.py#L59), so it would try to execute `--bg /`, not `btrfs balance start`. The `-dusage` value is discarded either way.
   Impact: the advertised balancing feature has never worked; a naive fix could launch an unbounded full balance on `/`.

3. ✅ **`my-restore` crashes on unlabeled filesystems.**
   [`select_mount()`](my_snaps/my_restore.py#L91) only matches quoted labels in [`label = mat.group(1)`](my_snaps/my_restore.py#L99). `btrfs filesystem show` prints `Label: none` for unlabeled filesystems, so `label` is unbound and the tool dies with `UnboundLocalError`. I reproduced this. For later filesystems, the previous label leaks instead. Since unlabeled BTRFS is common, this alone explains much of the lack of adoption.

4. ✅ **The mount-selection command is destructive and wrong.**
   [`umount /dev`](my_snaps/my_restore.py#L109) should be `umount /mnt`. As root, unmounting `/dev` can tear down devtmpfs or fail and short-circuit the `&&`, leaving the requested filesystem unmounted. Then [`select_restores()`](my_snaps/my_restore.py#L193) does `os.chdir('/mnt')` and operates on whatever is there. This is a safety-critical path, and it is executed with `force=True` even in dry-run.

5. ✅ **`my-restore --dry-run` is not dry.**
   [`get_state()`](my_snaps/my_restore.py#L141) directly runs `btrfs sub del` on any top-level `*ToDel` subvolume at [`continue`](my_snaps/my_restore.py#L169) with no confirmation and no dry-run check. The README itself says to delay deletion until a restore passes muster. The mount selection is also forced. A user invoking `-n` to inspect state can still delete data and change mounts.

6. ✅ **Restoring a different snapshot after a revert fails on a running system.**
   In the revert branch, [`prep = f'btrfs sub del "{subvol}" && '`](my_snaps/my_restore.py#L226) tries to delete the currently mounted root/home subvolume. `btrfs sub del` will fail with “device or resource busy,” and `&&` prevents the snapshot creation. The recovery loop described in [`README.md`](README.md#L105) therefore does not work as written on the installed system.

### P1 — correctness, safety, and portability

7. ✅ **`rsync -a -del` does not delete stale `/efi` entries.** [`sync_cmd()`](my_snaps/my_restore.py#L198) generates `rsync -a -del -H` at [`line 201`](my_snaps/my_restore.py#L201); the intended option is `--delete`. I verified that an extra stale file remains in the destination with `-del`, so the `.efi-back` mirroring is incomplete. **Fixed:** [`efi_sync_cmd()`](my_snaps/my_restore.py#L294) emits `rsync -a -H --delete`, refuses an empty `.efi-back` or a `/efi` that is not a mount point, and runs before the rename to `=Reverted`; tests in [`tests/test_restore_efi_sync.py`](tests/test_restore_efi_sync.py).
8. ✅ **Python metadata disagrees with itself.** [`requires-python`](pyproject.toml#L14) says `>=3.8`; the README says Python 3.11+; the pinned `console-window` 2.0.0.10 requires `>=3.10`. On 3.8/3.9 the local install cannot resolve its dependency. Also prefer `console-window>=2.0.0.10,<3` over an exact pin: the dependency had 31 releases and five uploads within roughly 26 hours. **Fixed:** 3.11 is the declared baseline (`requires-python = ">=3.11"`, classifiers for 3.11–3.13) and the dependency is a range (`console-window >= 2.0.0.10, <3`).
9. ✅ **Root re-exec happens before argument parsing.** `my-snaps --help`, `my-restore -n`, and `bt-smart-balance --help` all invoke sudo before argparse. That is poor UX and makes `pip --user` installs fragile. Parse arguments first; only re-exec for actions that actually need root.
   **Fixed:** every entry point now exposes [`parse_args()`](my_snaps/main.py#L1025) and calls [`reexec_as_root()`](my_snaps/MyUtils.py#L62) only afterwards. `--help`/`--version` and invalid usage therefore never prompt; `my-restore -n` stays unprivileged; `bt-smart-balance` re-execs only for `-i`, because [`_run()`](my_snaps/bt_smart_balance.py#L26) already prefixes individual `btrfs` commands with `sudo`. Covered by [`tests/test_entrypoints.py`](tests/test_entrypoints.py).
10. **Non-interactive error paths crash instead of reporting.** [`_create_snap()`](my_snaps/main.py#L293) and [`_del_subvolume()`](my_snaps/main.py#L329) call `self.win.alert` on failure, but `self.win` is `None` in `-s`/cron mode. A real `btrfs` error is replaced by `AttributeError: 'NoneType' object has no attribute 'alert'`.
11. ✅ **`process.wait()` before `communicate()` could deadlock.** If `btrfs sub list` or `df` produced more than the pipe buffer, the child would block writing while the parent waited. **Fixed:** [`_slurp_command()`](my_snaps/main.py#L616) calls [`communicate()`](my_snaps/main.py#L627) exactly once and never calls `wait()`, so both pipes are drained concurrently. [`tests/test_slurp_command.py`](tests/test_slurp_command.py#L49) covers 1 MB written to stdout and to stderr, and asserts that `wait()` is never invoked before `communicate()`.
12. **Shell construction with `shell=True` and unquoted paths.** [`_slurp_command()`](my_snaps/main.py#L355) runs as root through a shell. The README only forbids `.` and `/` in subvolume names, not spaces or shell metacharacters. Prefer argv lists and `subprocess.run([...])`; reserve shell only for fixed strings.
13. **Mount discovery is not robust.** [`_mount_tmps()`](my_snaps/main.py#L415) mounts every BTRFS device writable under `/tmp/.btrfs` without forcing `subvolid=5`; a configured default subvolume can make the paths wrong. The device scan (`lsblk`) and `/proc/mounts` are cached for the process lifetime, so hot-plugged devices and later mounts are still missed until a refresh. Multi-device BTRFS filesystems appear as duplicate devices.
    *The multi-second startup stall reported for step 2 is fixed:* the scan used `blkid`, which probes every block device; on the development machine the idle optical drive alone cost ~17 of the ~18 seconds. `lsblk -P` answers from sysfs/udev in ~8 ms, so startup went from ~18.5 s to ~0.08 s with no cache to invalidate.
14. **Operational errors use `assert`.** [`assert self.snap_subvol`](my_snaps/main.py#L500) and the root-filesystem assertion vanish under `python -O`. Use explicit exceptions with actionable messages.
15. **Auto-cleanup of `*ToDel` is too eager.** [`get_state()`](my_snaps/my_restore.py#L141) deletes any top-level name ending in `ToDel`, not just ones this tool created, and does it at startup. Require a flag or explicit menu action.
16. **Menu arrow handling is duplicated.** In [`get_prompt_obj()`](my_snaps/InlineMenu.py#L290), `\033[C` appears in both the up/left and down/right branches at [`lines 302–305`](my_snaps/InlineMenu.py#L302), while `\033[D` is ignored. Right arrow always moves the wrong way, and left arrow does nothing.
17. **The `a` key hardcodes a limit of 8.** [`max(0, this_cnt-8+1)`](my_snaps/main.py#L281) ignores `add_limit` and is not obvious from the README. Non-interactive `-s` without `-L` also has a label mismatch (`None` vs `''`), so it can fail to remove old unlabeled snapshots.
18. **Smaller issues:** `select_mount` keys options by `str(idx)`, but the menu reads one character, so more than 9 filesystems breaks; `check_bootable` hardcodes 32-character entries and only matches your systemd-boot layout; `--print` calls `max()` on an empty row list; `-s` silently clamps to 8; `human()` returns odd boundaries like `99.9K` then `0.1M`; several docstrings are still `TBD`.

### P2 — maintenance and discoverability

- ⏳ No test suite, CI, tags, changelog, `SECURITY.md`, issue templates, or man page. For a root-owned destructive tool, that is the biggest adoption blocker. **Update:** a pytest suite (113 tests) and a CI workflow now exist; tags, changelog, `SECURITY.md`, issue templates, and a man page are still missing.
- Broad `pylint: disable` headers and “TBD” docstrings make contribution harder than the code size suggests.
- The GitHub description (“Basic upgrade and BTRFS tools for Fedora and EOS”) no longer matches the PyPI summary (“Simple BTRFS Snapshot Tools”).
- The local version was not bumped after the dependency change; `runner` is a debug convenience, not a test harness.
- License metadata is still the old `license = { text = "MIT" }` form; it works, but modern PEP 639 metadata is cleaner.

---

## How it compares

Popularity figures are as of 2026-10-24.

| Tool | Snapshot model | Strengths | Cost / gap | Fit for this use case |
|---|---|---|---|---|
| **my-snaps** | label + replace-eldest, TUI, rename-based restore | Tiny, no daemon, transparent names, tailored `.efi-back` workaround | Silent-failure bug, restore defects, no tests, one maintainer | **Keep personal** |
| **snapper** (+ snap-pac, grub-btrfs) | timeline, config retention, rollback | Mature, distro integration, boot-menu rollback (~1.2k stars) | Config/DBus/units, more moving parts | Best if you want point-and-click rollback |
| **btrbk** | snapshot + send/receive + retention | Proven offsite backups, flexible retention (~2.2k stars) | Config-heavy, no TUI restore | Use for backups, not daily updates |
| **btrfsmaintenance** | balance/scrub/trim jobs | Maintained, correct, systemd/cron integration (~1.1k stars) | Not a snapshot tool | **Replace `bt-smart-balance`** |
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
- You are willing to fix the P0s and add a small test suite. The design deserves that.

**Do not keep it as a public/general-purpose project unless you want to take on:**
- Portability across unlabeled/multi-device BTRFS, non-systemd-boot setups, and more than 9 filesystems.
- Tests, CI, tags, changelog, docs, and support.
- The trust bar for a root tool that deletes subvolumes. One silent failure is enough to lose users permanently.

**If you want less risk with less maintenance:**
- Keep `my-snaps` for snapshots and `my-restore` for restores, but fix the P0s first.
- Replace `bt-smart-balance` with **btrfsmaintenance** unless you want to own that feature.
- Use **btrbk** for offsite/retention and **snapper + grub-btrfs/snap-pac** only if you want boot-menu rollback.
- Add a short “personal, unsupported, Linux-only, BTRFS-only” note to the README. It sets expectations and is honest about the project’s scope.

---

## Minimum fix list, in order

1. ✅ **Fix `_slurp_command()`:** `out, err = process.communicate()`, then return `process.returncode`; decode with `errors='replace'`.
2. ✅ **Fix `balance_filesystem()`:** use `self.opts.dusage`, keep the full `btrfs balance start -dusage=... --bg ...` command, and guard `device_size == 0`.
3. ✅ **Fix `select_mount()`:** initialize `label = ''` per filesystem, accept `Label: none`, and change `umount /dev` to `umount /mnt`.
4. ✅ **Fix restore-after-revert:** rename the current subvolume to `.ToDel` instead of `btrfs sub del` on a mounted subvolume, or refuse with a clear message.
5. ✅ **Make dry-run dry:** skip `*ToDel` deletion and forced mounts when `self.dry_run` is set.
6. ✅ **Fix `rsync -a -del` → `rsync -a --delete`**. Done as `rsync -a -H --delete`, with guards that refuse an empty `.efi-back` or an unmounted `/efi` ([`efi_sync_cmd()`](my_snaps/my_restore.py#L294)); see defect 7.
7. ✅ **Bump the version and align metadata:** `requires-python = ">=3.10"` (or 3.11 per README), `console-window>=2.0.0.10,<3`. Done: 3.11 is the baseline (`requires-python = ">=3.11"`, classifiers 3.11–3.13) and the dependency is the requested range; the version is already `2.0.0`.
8. ✅ **Parse args before sudo**, and add `--version`. Done: all three entry points parse first, `--version` is available everywhere, and escalation is centralized in [`reexec_as_root()`](my_snaps/MyUtils.py#L62); `my-restore -n` and a normal `bt-smart-balance` check never re-exec.
9. **Guard `self.win`** in non-interactive paths; print errors to stderr and exit nonzero.
10. **Replace `assert` with explicit errors**, quote paths or use argv lists, and add `subvolid=5` to temporary mounts.
11. ✅ **Add a pytest suite with a fake `btrfs` executable in `PATH`** covering: nonzero exit propagation, `Label: none` parsing, restore command generation, retention counts, dry-run behavior, and the `*ToDel` policy. Done: 113 tests in [`tests/`](tests), including fake `btrfs`/`lsblk`/`rsync` executables and the `/efi` mirror guards.
12. ⏳ **Add GitHub Actions** running ruff and pytest on 3.10–3.13; tag `v1.1.0`; add a CHANGELOG and update the stale GitHub description. CI exists at [`.github/workflows/ci.yml`](.github/workflows/ci.yml) but runs pytest only, on Python 3.11 alone; the ruff step, wider matrix, tag, CHANGELOG, and description update are still open.

The single most important sentence in this audit: **fix item 1 before the next system update.** The tool currently cannot tell you whether the snapshot it just created actually exists.
