# jdef-fedora-tools README
> **NOTE: This is not quite ready for consumption as of 2023-11-29. Wait until this notice is removed to even consider using or cloning.**

> Basic upgrade and BTRFS tools for Fedora

## Introduction
Three tools are provided Fedora maintenance assuming you've install on the default BTRFS:
* `my-upgrade` steps you through updating the current point release or upgrading to the next major point release. `my-upgrade` is very much Fedora specific.
* `my-snaps` which lets you create snapshots and replace the snapshots for the simplest BTRFS use cases.
* `my-restore` which assists restoring snapshots to back out changes to the system
* `my-snaps` and `my-restore` are not necessarily Fedora specific, but you may need to adjust a few instructions to use them on another distro.

**Installation.** To install into `/usr/local/bin` (which is hopefully on your PATH):
```
  sudo dnf install git  # python 3.8 or later is required
  cd ~  # or anywhere desired (e.g., ~/Projects)
  git clone git@github.com:joedefen/jdef-fedora-tools.git
  ./jdef-fedora-tools/deploy  # NOTE: use "undeploy" script to reverse an install
```
* you must keep the source directory (`deploy` creates symbolic links to it tools).
* within the source directory, you can run `git pull` to update to the latest.
---

## my-upgrade
`my-upgrade` steps you through upgrading your system. When run the main screen looks like this:

![my-update-p1.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-update-p1.png?raw=true)
* *if you cannot see the full menu, enlarge the window else navigation is confusing*
* to execute the line, highlight and press ENTER.
* to highlight a line, press its key (on the left) or use the up/down arrow keys
* after an execution, you will be placed on the next item, but you can easily choose to repeat or skip steps
* executing `0` runs `my-snaps` describe below.
* executing `2` takes you to another menu (shown below)
* normally, you execute either `4` or `5`
* if no flatpaks, skip `7` and `8`
* step `8` need not be done every update, as you wish.

Choosing `RELEASE UPGRADE` offers this screen:

![my-update-p2.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-update-p2.png?raw=true)

* `b` and `e` cause reboots; you must remember where you are, re-run `my-upgrade` and navigate to the next step.
* `g` and beyond are "advanced steps" and somewhat optional.
* if there are "unsatisfied", use a separate window to erase them if you please
* See [Upgrading Fedora Using DNF System Plugin :: Fedora Docs](https://docs.fedoraproject.org/en-US/quick-docs/upgrading-fedora-offline/) for more details.

---

## my-snaps
`my-snaps` can be used for simple snapshot maintenance. After running, it may look like this:
![my-snaps.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-snaps.png?raw=true)

* In the header, the BTRFS partitions are shown with `df -h` info (showing Size, Used, Avail, Use%, and Mounted on); run df separate to remind you of the fields when needed.
* All snapshots are expected to be in `/.snapshots/
* Snapshots are to be named {golden}.YYYY-MM-DD-HHmmss where YYYY, etc., are numerics representing time fields (i.e., a period plus only digits, dashes, and colons).
* On your very first run, highlight each subvolume for which you wish snapshots, and press `s` to create one.
* On subsequent runs, you can quickly replace all your snapshots (i.e., remove the old one(s), and create new ones) by pressing `r`.
* Some other keys are:
  * `d`: to remove highlighted subvolume (usually pick a snapshot); you cannot remove mounted subvolumes; if there are nested subvolumes, those are removed to.
  * `u`: to get disk usage (this can take quite a while and is not perfect)
  * `?`: to get help on all keys and navigation
* NOTE: actions require confirmation to ensure accidental keys do not clobber your system.
---

## my-restore
`my-restore` is used to restore one or more of your snapshots. When launched, you see something like this:

![my-restore-p1.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-restore-p1.png?raw=true)

Choose the desired BTRFS partition to mount on `/mnt` (after running `umount /mnt` if occupied).
Next you'll see a screen like this:

![my-restore-p2.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-restore-p2.png?raw=true)

* unless you have a very wide screen, the commands will be truncated, but ensure you can see the snapshots names after `/.snapshots`; basically, you are
  * creating new writeable snapshots from your saved snapshots, first named {subvol}.old
  * and then {subvol} is renamed {subvol}.new (new is the one you wish to back out)
  * and then {subvol}.old is renamed {subvol} so that the restored subvolume becomes master.

* highlight and press enter the snapshots to be restored
* when done with snapshot restorals, reboot the system
* **important cleanup**: after the reboot, run some tests and, if happy with the back-out, then run `my-snaps` and remove and `.new` or `.old` subvolumes.

**--- Restore Special Cases ---**

**An update will not boot.**  If an update will not boot:
* boot the live installer.
* install these tools per the instructions.
* restore the appropriate snapshots using `my-restore` and reboot

**A back-out needs undone.**  If you regret doing a back-out, then manually:
* `sudo -i` # become root in effect (take care)
* `ls-blk -f` # to help identify the partition holding your system BTRFS
* `mount {your-btrs-partition} /mnt`
* `cd /mnt; ls`  # enter /mnt, and view the names of the subvolumes
* for every `.new` entry, move it back into place; e.g., for a subvolume called `fedora@root`,
  * `mv fedora@root fedora@root.old; mv fedora@root.new fedora@root`
* `reboot now`

## Final Thoughts
* `my-snaps` and `my-restore` support the most simple BTRFS snapshot strategy (for update protection and limited file recovery).  To guard against huge catastrophes, add complementary strategies such as these so you can quickly reinstall if needed:
  * keep your important document it the cloud (e.g., Google Drive)
  * keep critical configuration on github (e.g., using [How to Store Dotfiles - A Bare Git Repository | Atlassian Git Tutorial](https://www.atlassian.com/git/tutorials/dotfiles) or a dotfile manager).
  * create a list or script of post-install tasks to add/remove apps (save with your dotfiles or in the cloud)
* BTW, the above complementary strategies have even more value if you have multiple installs (e.g., a desktop, a 24/7 server, and a few laptops all running similarly with the same apps an basic config).
* For more complete protection, consider `snapper`, `btrbk`, `btrfsmaintenance`, and google for others.

