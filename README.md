# jdef-fedora-tools README
> Basic upgrade and BTRFS tools for Fedora

## Introduction
Three tools are provided Fedora maintenance assuming you've installed on the default BTRFS:
* `my-upgrade` steps you through updating the current point release or upgrading to the next major point release. `my-upgrade` is very much Fedora specific and does not require you to use `my-snaps` although integrated.
* `my-snaps`  assists creating snapshots and replacing the snapshots for the simplest BTRFS use cases (e.g., just before software updates). You can schedule period snapshots with the additional tool, `my-snaps-cronjob`.
* `my-restore` assists restoring snapshots to back out changes to the system
* `my-snaps` and `my-restore` are not necessarily Fedora specific, but you may need to adjust a few instructions to use them elsewhere.

**Installation.** To install into `/usr/local/bin` (which is hopefully on your PATH):
```
  sudo dnf install git  # also, python 3.8 or later is required
  cd ~  # or anywhere desired (e.g., ~/Projects)
  git clone https://github.com/joedefen/jdef-fedora-tools.git
  ./jdef-fedora-tools/deploy  # NOTE: use "undeploy" script to reverse an install
```
* you must keep the source directory (`deploy` creates symbolic links to the tools).
* within the source directory, run `git pull` to update to the latest
* after install, run some tests per "Initial and Regression Testing"

*Note that no separately installed python modules are required.*  This adds extra code for the menus, but the benefit nothing except new `python3` incompatibilities will break the tools. Experience suggests this precaution is well justified.

---

## my-upgrade
`my-upgrade` steps you through upgrading your system. Its main screen looks like this:

![my-update-p1.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-update-p1.png?raw=true)
* *if you cannot see the full menu, enlarge the window else navigation is confusing*
* to execute the line, highlight and press ENTER.
* to highlight a line, press its key (on the left) or use the up/down arrow keys
* after an execution, you will be placed on the next item, but you can easily choose to repeat or skip steps
</br>
* executing `a` runs `my-snaps` described below.
* executing `c` takes you to another menu (shown below)
* normally, you execute either `e` or `f`
* if no flatpaks, skip `h` and `j`

Choosing `RELEASE UPGRADE` offers this sub-menu:

![my-update-p2.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-update-p2.png?raw=true)

* `b` and `e` cause reboots; note the reboot step, re-run `my-upgrade`, and navigate to the next step.
* `g` and beyond are "advanced steps" and somewhat optional.
* if there are "unsatisfied" packages, use a separate window to erase them if you please
* See [Upgrading Fedora Using DNF System Plugin :: Fedora Docs](https://docs.fedoraproject.org/en-US/quick-docs/upgrading-fedora-offline/) for more details.

---

## my-snaps
`my-snaps` can be used for simple snapshot maintenance. After running, it may look like this:

![my-snaps.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-snaps.png?raw=true)

* In the header, the BTRFS partitions are shown with `df -h` info (showing Size, Used, Avail, Use%, and Mounted on); run df separate to remind you of the fields when needed.
* All snapshots are expected to be in `/.snapshots/`
* Snapshots are to be named `{subvol}.YYYY-MM-DD-HHmmss` where `YYYY` are time fields separated by dashes or colons only PLUS and optional "label" that begins with `=`.
* On your very first run, highlight each subvolume for which you wish snapshots, and press `s` to create one.
* On subsequent runs, `r` replaces your eldest snapshot of the same label for each top-level subvolume that has any snapshots.
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

**Periodic Snapshots**: The included tool, `my-snaps-cronjob` can be added to cron (with `crontab -e`) to set up periodic snapshots; read `my-snaps-cronjob` for details.


---

## my-restore
`my-restore` is used to restore one or more of your snapshots. When launched, you see something like this:

![my-restore-p1.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-restore-p1.png?raw=true)

Choose the desired BTRFS partition to mount on `/mnt` (after running `umount /mnt` if occupied).

Next you'll see a screen like this:

![my-restore-p2.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-restore-p2.png?raw=true)

* unless you have a very wide screen, the commands will be truncated, but ensure you can see the snapshots names after "RESTORE"; when restoring, you are
  * creating new writeable snapshots from your saved snapshots, first named {subvol}.old
  * and then {subvol} is renamed {subvol}.new (new is the one you wish to back out)
  * and then {subvol}.old is renamed {subvol} so that the restored subvolume becomes current.

* highlight and press enter the subvolumes to effect the given action:
  * RESTORE promotes the snapshot to current
  * UN-RESTORE demotes a restored snapshot to {subvol}.old and rename {subvol}.new as {subvol}
  * RE-RESTORE undoes a UN-RESTORE by renaming {subvol} as {subvol}.new and {subvol}.old as {subvol}
* **unless you see some UN-RESTORE actions list, there are no pending changes**
* when you are done with setting up the snapshot restorals, reboot the system

**------------- IMPORTANT NOTES -------------**

**When a restore is tested and deemed satisfactory then clean up.**
* To clean up, launch `my-snaps` and remove the `.new` or `.old` subvolumes.
* **Warning**: Failing to clean up will confuse the next `my-restore` and which may suggest undoing your successful restore.

**When a restore is tested and deemed unsatisfactory:**
* Launch `my-snaps` and adjust the restored snapshots as desired.
* Then reboot, test, and iterate until you are done and then clean up (described immediately above).

**In the case that an update or restore will not boot, then**:
* boot the live installer.
* install these tools per the instructions.
* use `my-restore` to return to a working system and reboot and eventually clean up again.
* if snapshots alone will not do, read the following section on `/boot` and `/boot-efi`.

### How /boot and /boot/efi Placement affects Restores
Personally, I place `/boot` within my root BTRFS partition (although not a Fedora standard). This causes a grub warning that can be suppressed by running `sudo grub2-editenv - unset menu_auto_hide` (however mysterious that seems).  Consider using that pattern on new installs and retrofitting old installs.

**But, if /boot is a separate ext4 partition, there are greater chances of a restore not working.** Generally, when you restore an older snapshot (especially one you just created), its Linux version will still be present ... so, in the boot menu, if the newer OS version will not boot, iteratively try older versions until one works.  In the worst case, you'll need to do a **boot repair**; search for "*Restoring the bootloader using the Live disk*" in [Fedora Linux User Documentation :: Fedora Docs](https://docs.fedoraproject.org/en-US/fedora/latest/).

As part of `my-upgrade`, it copies `/boot/efi` (the UEFI boot partition) to `/boot/efi.{date}` and removes old versions. In some cases, that might help repair `/boot/efi`; but if not, the boot repair from the Live disk procedure is the definitive fix.

---

## Initial and Regression Testing
Do not assume the BTRFS scripts work for you and then be in a pickle later. After install:
* Run `my-snaps` and ensure:
  * all the top-level subvolumes are showing, and
  * the expected snapshots are showing, and
  * add snapshots for the volumes you may wish to restore.

* If `my-snaps` is working, run `my-restore` and, after device selection, that the RESTORE entries look right, and
* If all looks well, RESTORE and then UN-RESTORE one of your subvolumes (leaving all subvolumes in RESTORE or RE-RESTORE state)
* After the tests, run `my-snaps` again and remove the .old subvolumes (if there are any .new subvolumes UN-RESTORE them with `my-restore` and repeat).

If there are issues, ensure snapshots are in `/.snapshots` and they are named `{subvol}.{timespec}` where `{timespec}` has only numbers, dashes and colons.

---

## Best Practices for Using this Simple Snapshot Strategy
* `my-snaps` and `my-restore` support the most simple BTRFS snapshot strategy (for update protection and limited file recovery).  To guard against huge catastrophes, add complementary strategies such as these so you can quickly reinstall if needed:
  * keep your important document it the cloud (e.g., Google Drive)
  * keep critical configuration on github (e.g., using [How to Store Dotfiles - A Bare Git Repository | Atlassian Git Tutorial](https://www.atlassian.com/git/tutorials/dotfiles) or a dotfile manager).
  * create a list or script of post-install tasks to add/remove apps (save with your dotfiles or in the cloud)
* BTW, the above complementary strategies have even more value if you have multiple installs (e.g., a desktop, a 24/7 server, and a few laptops all running similarly with the same apps an basic config).
* For more comprehensive protection, consider `snapper`, `btrbk`, `btrfsmaintenance`, and google for others.

