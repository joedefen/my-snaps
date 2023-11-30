# jdef-fedora-tools README
> Basic upgrade and BTRFS tools for Fedora

## Introduction
Three tools are provided Fedora maintenance assuming you've install on the default BTRFS:
* `my-upgrade` which step you thru the commands to update the current point release or step up to the next major point release.
* `my-snaps` which lets you create snapshots and replace the snapshots for the simplest cases.
* `my-restore` which assists restoring snapshots to back out changes to the system.

## my-upgrade
`my-upgrade` steps you through upgrading your system. When run the main screen looks like this:

![my-update-p1.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-update-p1.png?raw=true)
* *if you cannot see the full menu, enlarge the window else navigation is confusing*
* to execute the line, highlight and press ENTER.
* to highlight a line, press its key (on the left) or use the up/down arrow keys
* after an execution, you will be placed on the next item, but you can easily choose to repeat or skip steps

**Notes**:
* executing `0` runs `my-snaps` describe below.
* executing `2` takes you to another menu (shown below)
* normally, you execute either `4` or `5`
* if no flatpaks, skip `7` and `8`
* step `8` need not be done every update, as you wish.

Choosing `RELEASE UPGRADE` offers this screen:

![my-update-p2.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-update-p2.png?raw=true)
**Notes**:
* `b` and `e` cause reboots; you must remember where you are, re-run `my-upgrade` and navigate to the next step.
* `g` and beyond are "advanced steps" and somewhat optional.
* if there are "unsatisfied", use a separate window to erase them if you please
* See [Upgrading Fedora Using DNF System Plugin :: Fedora Docs](https://docs.fedoraproject.org/en-US/quick-docs/upgrading-fedora-offline/) for more details.

## my-snaps
`my-snaps` can be used for simple snapshot maintenance. After running, it may look like this:
![my-snaps.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-snaps.png?raw=true)
**Notes**:
* In the header, the BTRFS paritions are shown with `df -h` info (showing Size, Used, Avail
* , Use%, and Mounted on).
* All snapshots are expected to be in `/.snapshots/`
* On first run, highlight each subvolume for which you wish to snapshot, and press `s` to create one.
* On susquent runs, to replace the snaps (i.e., remove the old one(s), and create a new), press `r`.
* Some other keys are:
  * `d`: to remove highlighted snapshot
  * `u`: to get disk usage (this can take quite a while)
  * `?`: to get help on all keys and navigation

# my-restore
![my-restore-p1.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-restore-p1.png?raw=true)
![my-restore-p2.png](https://github.com/joedefen/jdef-fedora-tools/blob/main/images/my-restore-p2.png?raw=true)

