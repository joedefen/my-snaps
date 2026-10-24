# Audit of `my-snaps`

**Audit date:** 2026-10-24  
**Last verified against the working tree:** 2026-10-24, after the P0 fixes  
**Scope:** the working tree in this repository (not only the published PyPI artifact).  
**Method:** source review of every module, `pytest` (130 tests, all passing), and the targeted non-destructive reproductions recorded in the first pass (monkeypatched subprocesses and a temporary `rsync` directory). No `btrfs` commands were run and no mounts were touched.

---

## Bottom line

Keep it, but keep it as a **personal, opinionated tool** for now. Every P0 defect from the first pass is fixed and covered by tests: command exit codes now propagate, `bt-smart-balance` builds a real bounded balance, and the restore path no longer deletes during inspection, forces mounts, or fails after a revert. What remains is P1 polish (shell quoting, mount discovery, `assert`-based errors, menu key handling) plus the usual packaging work. None of the open items can silently report a failed `btrfs` command as success, which was the reason the earlier version should not have been recommended to strangers.

---

## What the project is, in evidence

- 2,280 lines across five modules ([`main.py`](my_snaps/main.py) 1,113; [`my_restore.py`](my_snaps/my_restore.py) 453; [`InlineMenu.py`](my_snaps/InlineMenu.py) 402; [`bt_smart_balance.py`](my_snaps/bt_smart_balance.py) 207; [`MyUtils.py`](my_snaps/MyUtils.py) 105), one runtime dependency, MIT.
- 130 pytest tests in [`tests/`](tests) and one GitHub Actions workflow ([`.github/workflows/ci.yml`](.github/workflows/ci.yml#L1)) that runs pytest on Python 3.11 only.
- No git tags, no changelog, no `SECURITY.md`, no issue templates, no man page.
- [`pyproject.toml`](pyproject.toml#L7) declares 2.0.0, `requires-python = ">=3.11"`, and a dependency range of `console-window >= 2.0.0.10, <3`; nothing in the repository ties that version to a released commit.
- Three console scripts: `my-snaps`, `my-restore`, `bt-smart-balance`.

---

## What is genuinely worth keeping

1. **The mental model is excellent.** Labels (`=Update`, `=Daily`) plus “replace the eldest of the same label” is a clearer description of the pre-update use case than snapper’s timeline terminology. The naming convention is transparent and greppable.
2. **Tiny maintenance surface.** No daemon, no DBus, no systemd unit, no database, one dependency. The entire thing can be read in an evening.
3. **The restore theory is simple and reversible.** Rename the current subvolume to `=Reverted`, create a writable subvolume from the read-only snapshot, reboot. That is much easier to reason about than a bootloader-integrated rollback.
4. **The `.efi-back` workaround is a real differentiator** for your systemd-boot + pacman + dracut setup, documented in [`README.md`](README.md#L155). Snapper, Timeshift, and btrbk do not solve that out of the box.
5. **Both interaction modes are useful.** The curses TUI for “before I update,” and `-p`/`-s`/`--cron` for automation, with no config file.
6. **The regression suite is real.** 130 tests run without a BTRFS filesystem, a mount, or root by substituting fake `btrfs`/`lsblk`/`rsync` executables. That is what makes the remaining fixes cheap.
7. **It is yours.** That is a legitimate reason to keep it. A 2-star personal tool that fits your workflow is not a failure; it is a tool.

---

## Defects found

> **Status as of 2026-10-24:** every P0 item from the first pass is fixed in the working tree and covered by tests. What follows is only what is still open; defect numbering restarts at 1.

### P1 — correctness, safety, and portability

1. **Shell construction with `shell=True` and unquoted paths.** [`_slurp_command()`](my_snaps/main.py#L616) still runs as root through a shell, and clients interpolate paths into fixed strings: [`_create_snap()`](my_snaps/main.py#L548) builds `btrfs sub snap -r {mount} {snap_path}` at [`line 577`](my_snaps/main.py#L577), and [`_ensure_tmp_mount()`](my_snaps/main.py#L742) runs `mount {dev_node} {tmp_mount_dir}` via `os.system()` at [`line 756`](my_snaps/main.py#L756). The README only forbids `.` and `/` in subvolume names and labels, not spaces, quotes, or shell metacharacters. Prefer argv lists and `subprocess.run([...])`; reserve the shell for fixed strings.

2. **Mount discovery is not robust.** [`_ensure_tmp_mount()`](my_snaps/main.py#L742) mounts each BTRFS device writable under `/tmp/.btrfs` and without forcing `subvolid=5`, so a configured default subvolume can make the paths wrong; the README even describes these as “read-only bookkeeping mounts” ([`README.md`](README.md#L64)). [`_scan_block_devices()`](my_snaps/main.py#L662) caches its result for the process lifetime ([`line 673`](my_snaps/main.py#L673)) and [`_load_mounts()`](my_snaps/main.py#L793) caches `/proc/mounts` ([`line 795`](my_snaps/main.py#L795)), so hot-plugged devices and later mounts are missed until restart. [`_load_devs()`](my_snaps/main.py#L683) keys devices by block-device name, so a multi-device BTRFS filesystem appears as several devices.

3. **Operational errors use `assert`.** [`_determine_mount_points()`](my_snaps/main.py#L826) uses `assert False, f'root is not BTRFS (...)'` at [`line 829`](my_snaps/main.py#L829) and `assert self.snap_subvol` at [`line 852`](my_snaps/main.py#L852). Both vanish under `python -O`, turning a clear refusal into an `AttributeError` later. Use explicit exceptions with actionable messages.

4. **Menu arrow handling is duplicated.** In [`get_prompt_obj()`](my_snaps/InlineMenu.py#L300), `\033[C` appears in both the up/left branch at [`line 312`](my_snaps/InlineMenu.py#L312) and the down/right branch at [`line 315`](my_snaps/InlineMenu.py#L315), while `\033[D` is ignored. The right arrow is ambiguous and the left arrow does nothing.

5. **The `a` key hardcodes a limit of 8.** [`discard = max(0, this_cnt-8+1)`](my_snaps/main.py#L537) ignores `add_limit` and is not documented in the README. Non-interactive `-s` without `-L` also has a label mismatch: [`self.label`](my_snaps/main.py#L45) stays `None`, while [`label_groups`](my_snaps/main.py#L451) is keyed by `''`, so old unlabeled snapshots are never removed. Separately, `-s` silently clamps to 8 in [`parse_args()`](my_snaps/main.py#L1081).

6. **Smaller issues.** [`mount_choices()`](my_snaps/my_restore.py#L145) keys options by `str(idx)` at [`line 162`](my_snaps/my_restore.py#L162), but the menu reads one character, so more than 9 filesystems cannot be selected unambiguously. [`check_bootable()`](my_snaps/my_restore.py#L189) hardcodes 32-character entries at [`line 200`](my_snaps/my_restore.py#L200) and only matches your systemd-boot layout. `--print` calls `max()` on an empty row list in [`calc_path_width()`](my_snaps/main.py#L971) (and the other width helpers). [`human()`](my_snaps/MyUtils.py#L20) returns odd boundaries like `99.9K` then `0.1M` ([`line 27`](my_snaps/MyUtils.py#L27)). Several docstrings are still `TBD`, e.g. [`BTRFS`](my_snaps/main.py#L35), [`gather_snapshots()`](my_snaps/main.py#L864), and [`BtrfsRestore`](my_snaps/my_restore.py#L73).

### P2 — maintenance and discoverability

- A pytest suite (130 tests) and a CI workflow exist, but [`.github/workflows/ci.yml`](.github/workflows/ci.yml#L1) runs pytest only, on Python 3.11 alone, with no ruff step. Tags, changelog, `SECURITY.md`, issue templates, and a man page are still missing.
- Broad `pylint: disable` headers and “TBD” docstrings make contribution harder than the code size suggests.
- The GitHub description (“Basic upgrade and BTRFS tools for Fedora and EOS”) no longer matches the PyPI summary (“Simple BTRFS Snapshot Tools”).
- No tag or changelog anchors the 2.0.0 version in [`pyproject.toml`](pyproject.toml#L7); `runner` is a debug convenience, not a test harness.
- License metadata is still the old `license = { text = "MIT" }` form ([`pyproject.toml`](pyproject.toml#L13)); it works, but modern PEP 639 metadata is cleaner.

---

## How it compares

Popularity figures are as of 2026-10-24.

| Tool | Snapshot model | Strengths | Cost / gap | Fit for this use case |
|---|---|---|---|---|
| **my-snaps** | label + replace-eldest, TUI, rename-based restore | Tiny, no daemon, transparent names, tailored `.efi-back` workaround, 130-test regression suite | Shell-quoting and mount-discovery rough edges, no tags/changelog/man page, one maintainer | **Keep personal** |
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
- You are willing to finish the P1 items. None is a redesign, and the test suite now makes each one cheap to verify.

**Do not keep it as a public/general-purpose project unless you want to take on:**
- Portability across unlabeled/multi-device BTRFS, non-systemd-boot setups, and more than 9 filesystems.
- Tests, CI (ruff plus a wider Python matrix), tags, changelog, docs, and support.
- The trust bar for a root tool that deletes subvolumes. The silent-failure class is now covered by tests, but unquoted shell paths and default-subvolume mount discovery are the same category of risk.

**If you want less risk with less maintenance:**
- Keep `my-snaps` for snapshots and `my-restore` for restores; finish P1 items 1–5 first.
- Keep `bt-smart-balance`: it now launches a bounded `-dusage` background balance and refuses to start a second one. Use **btrfsmaintenance** instead if you want a maintained job runner.
- Use **btrbk** for offsite/retention and **snapper + grub-btrfs/snap-pac** only if you want boot-menu rollback.
- Add a short “personal, unsupported, Linux-only, BTRFS-only” note to the README. It sets expectations and is honest about the project’s scope.

---

## Minimum fix list, in order

1. **Quote paths or use argv lists.** Convert [`_slurp_command()`](my_snaps/main.py#L616) clients to `subprocess.run([...])`, starting with `_create_snap()` and `_ensure_tmp_mount()`.
2. **Fix temporary mounts.** Add `-o subvolid=5` (and `-o ro` if the README’s claim is the intent) in [`_ensure_tmp_mount()`](my_snaps/main.py#L742); stop caching the device scan and `/proc/mounts` across refreshes, and deduplicate multi-device filesystems.
3. **Replace `assert` with explicit errors** in [`_determine_mount_points()`](my_snaps/main.py#L826).
4. **Fix menu arrows** in [`get_prompt_obj()`](my_snaps/InlineMenu.py#L300): one branch per arrow, handling `\033[D` as well as `\033[C`.
5. **Make `a` honor `add_limit`**, normalize the `None`/`''` label mismatch for unlabeled `-s`, and report (rather than silently apply) the clamp at [`parse_args()`](my_snaps/main.py#L1081).
6. **Sweep the small items:** single-key selection for more than 9 filesystems, a layout-independent `check_bootable()`, empty-row handling in the width helpers, `human()` boundaries, and the remaining `TBD` docstrings.
7. **Extend CI:** add a ruff step and run the matrix on 3.11–3.13; tag the release that matches 2.0.0 and add a CHANGELOG. Update the stale GitHub description.
8. **Add project hygiene:** `SECURITY.md`, issue templates, a man page, and PEP 639 license metadata.

The most valuable remaining work is item 1: the snapshot path still builds root-privileged shell strings from subvolume names, and the README does not forbid the characters that would break them.
