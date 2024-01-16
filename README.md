# snap-tools README
> Tools for managing Btrfs snapshots

* `my-snaps`  assists creating snapshots and replacing the snapshots for the simplest BTRFS use cases (e.g., just before software updates).
* You can schedule period snapshots with the additional tool, `daily-snaps`.
* `my-restore` assists restoring snapshots to back out changes to the system

**Installation.** To install into `/usr/local/bin` (which is hopefully on your PATH):
```
  # note: python 3.8 or later is required
  # install git  if needed
  cd ~  # or anywhere desired (e.g., ~/Projects)
  git clone https://github.com/joedefen/my-snaps.git
  ./update-tools/deploy  # NOTE: use "undeploy" script to reverse an install
```
* you must keep the source directory (`deploy` creates symbolic links to the tools).
* within the source directory, run `git pull` to update to the latest
* after install, run some tests per "Initial and Regression Testing"

*Note that no separately installed python modules are required.*  This adds extra code for the menus, but the benefit nothing except new `python3` incompatibilities will break the tools. Experience suggests this precaution is well justified.

---

## my-snaps
`my-snaps` can be used for simple snapshot maintenance. After running, it may look like this:

[comment]: ![my-snaps.png](https://github.com/joedefen/update-tools/blob/main/images/my-snaps.png?raw=true)
![my-snaps.png](images/my-snaps.png)

* In the header, the BTRFS partitions are shown with `df -h` info (showing Size, Used, Avail, Use%, and Mounted on); run df separate to remind you of the fields when needed.
* All snapshots are expected to be in snapshot only subvolume mounted at `/.snapshots/`
* Snapshots are to be named `{subvol}.YYYY-MM-DD-HHmmss` where `YYYY` are time fields separated by dashes or colons only PLUS and optional "label" that begins with `=`.
* On your very first run, highlight each subvolume for which you wish snapshots, and press `s` to create one.
* On subsequent runs, `r` replaces your eldest snapshot of the same label for each top-level subvolume that has any snapshots.
* On subsequent runs, `a` replaces a snapshot of the same label for each top-level subvolume that has any snapshots.
  * to describe snapshots, add a short label when prompted (e.g., "=preF40upg").
  * **note**: you cannot use the characters "." or "/" in the snapshot names and labels
* On subsequent runs, `a` adds one snapshot for each top-level subvolume that has any snapshots.

> **Labels** create sets of snapshots that independently managed. You can create unique labels that are only removed manually.

* Some other keys are:
  * `d`: to remove highlighted subvolume (usually pick a snapshot); you cannot remove mounted subvolumes; if there are nested subvolumes, those are removed too.
  * `u`: to get disk usage (this can take quite a while and is not perfect)
  * `?`: to get help on all keys and navigation
* **NOTE**: actions require confirmation to ensure accidental keystrokes do not clobber your system.

**Non-interactive use**: `my-snaps` can be run non-interactively with these options:
* `-p` or `--print` dumps your top-level subvolumes and their snapshots
* `-s{N}` or `--add-snap-max={N}` adds a new snapshot for each subvolume with snapshots and removes the eldest until there are no more than `{N}`.
* `-l{label}` or `--label={label}` to set the label of the snapshots involved.

**Periodic Snapshots**: The included scripts, `daily-snaps` and `weekly` may be copied to `/etc/cron.daily` and `/etc/cron.weekly` to be run by `anacron` which must be installed (but `cronie` or equivalent).

---

## my-restore
`my-restore` is used to restore one or more of your snapshots. When launched, you see something like this:

[comment]: ![my-restore-p1.png](https://github.com/joedefen/my-snaps-tools/blob/main/images/my-restore-p1.png?raw=true)
![my-restore-p1.png](images/my-restore-p1.png)

Choose the desired BTRFS partition to mount on `/mnt` (after running `umount /mnt` if occupied).

Next you'll see a screen like this:

[comment]: ![my-restore-p2.png](https://github.com/joedefen/update-tools/blob/main/images/my-restore-p2.png?raw=true)
![my-restore-p2.png](images/my-restore-p2.png)

* when restoring, you are
  * if there is a backed-out version, the target subvolume will be removed,
  * else the target subvolume is renamed "subvolume.YYYY-MM-DD-HHMMSS=Reverted";
  * a new writeable snapshot for subvolume is created from the snapshot

* highlight and press enter the subvolumes to effect the given action:
  * **restore**: promotes the snapshot to current
  * **revert**: removes the target subvolume and moves the reverted volume tip back into place
  * **del**: deletes the reverted subvolume tip; **note**: normally delay 'del' until you have a working restore and you are done with the restore;  when done, it is best to delete the "=Reverted" subvolume (to free space).
* **unless you see some UN-RESTORE actions list, there are no pending changes**
* when you are done with setting up the snapshot restorals, reboot the system

**------------- IMPORTANT NOTES -------------**

**When a restore is tested and deemed satisfactory then clean up.**
* To clean up, launch `my-snaps` or `my-restore` and delete "=Reverted" subvolumes.
* **Warning**: Failing to clean up will confuse the next `my-restore` and waste space.

**When a restore is tested and deemed unsatisfactory:**
* Launch `my-snaps` and adjust the restored snapshots as desired (e.g., try restoring a different snapshot or reverting the tip).
* Then reboot, test, and iterate until you are done and then clean up (described immediately above).

**In the case that an update or restore will not boot, then**:
* boot the live installer.
* install these tools per the instructions.
* use `my-restore` to return to a working system and reboot and eventually clean up again.
* if snapshots alone will not do, read the following section on `/boot` and `/boot-efi`.

---

## Initial and Regression Testing
Do not assume the BTRFS scripts work for you and then be in a pickle later. After install:
* Run `my-snaps` and ensure:
  * all the top-level subvolumes are showing, and
  * the expected snapshots are showing, and
  * add snapshots for the volumes you may wish to restore.

* If `my-snaps` is working, run `my-restore` and, after device selection, that the "restore" entries look right, and
* If all looks well, restore and then revert one of your subvolumes (returning all subvolumes as they were).

If there are issues, ensure snapshots are in a subvolume ending with `@snapshots` which is normally mounted at `/.snapshots`, and snapshots are named `{subvol}.{timespec}[=Label]` where `{timespec}` has only numbers, dashes and colons.

---

## Best Practices for Using this Simple Snapshot Strategy
* `my-snaps` and `my-restore` support the most simple BTRFS snapshot strategy (for update protection and limited file recovery).  To guard against huge catastrophes, add complementary strategies such as these so you can quickly reinstall if needed:
  * keep your important document it the cloud (e.g., Google Drive)
  * keep critical configuration on github (e.g., using [How to Store Dotfiles - A Bare Git Repository | Atlassian Git Tutorial](https://www.atlassian.com/git/tutorials/dotfiles) or a dotfile manager).
  * create a list or script of post-install tasks to add/remove apps (save with your dotfiles or in the cloud)
* BTW, the above complementary strategies have even more value if you have multiple installs (e.g., a desktop, a 24/7 server, and a few laptops all running similarly with the same apps an basic config).
* For more comprehensive protection, consider `snapper`, `btrbk`, `btrfsmaintenance`, and google for others.