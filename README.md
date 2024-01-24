> **Minimal Quick Start:**
> * On python 3.11+, install `pipx`, else `pip`, and per your distro.
> * With `pipx`, run `pipx upgrade my-snaps || pipx install my-snaps`
> * With `pip`, run `pip install --upgrade my-snaps --user`
> * If `~/.local/bin/` is not on your $PATH, then make it so.
> * Run `my-snaps`; for each subvolume that you wish to snapshot, create a snapshot with `=Update` suffix (and as many as you wish to keep normally).
> * Subsequently before updates, run `my-snaps` and type `r` (replace-all) to replace the eldest `=Update` snaps per subvolume with new snaps.

# my-snaps - Simple Tools for BTRFS snapshots
* `my-snaps`  assists creating snapshots and replacing the snapshots for the simplest BTRFS use cases (e.g., just before software updates).
* `my-restore` assists restoring snapshots to back out changes to the system

**Prerequisites.** To use these tools, you must have an appropriate BTRFS setup:
* Your mounted top-level subvolume names must not contain '.' (i.e., periods).
* Your snapshot names must be one of the forms:
  * `{subvolume}.{date-string}`
  * `{subvolume}.{date-string}={label}`
* Your snapshots must reside in a top-level subvolume ending with "snapshots"; and it must normally be mounted at `/.snapshots`
* Labels define sets of snapshots that independently managed.
  * **note**: you cannot use the characters "." or "/" in the snapshot names and labels
  * Suggested labels:
    * **=Update** - created prior to a system update
    * **=Daily** - scheduled daily snapshots (and similarly for other periods)

Here is a compliant BTRFS setup showing it subvolumes/snapshots:
```
top-levels: eos@cache  eos@home  eos@log  eos@my-opt  eos@root  eos@snapshots

eos@snapshots:
'eos@home.2024-01-13-093817=Update'    'eos@root.2024-01-10-174732=Update'
'eos@home.2024-01-14-085102=Daily'     'eos@root.2024-01-13-084102=Daily'
'eos@home.2024-01-15-201554=Update'    'eos@root.2024-01-13-093817=Update'
'eos@my-opt.2024-01-13-093817=Update'  'eos@root.2024-01-14-085102=Daily'
'eos@my-opt.2024-01-14-085102=Daily'
```

**Installation.**
* **If `python3 -V` shows v3.11 or later, install using `pipx`**:
* `python3 -m pip install --user pipx # if pipx not installed`
  * `python3 -m pipx ensurepath # if needed (restart terminal)`
  * `pipx upgrade my-snaps || pipx install my-snaps`
* **Else for python3.10 and lesser versions, install using `pip`**:
  * `sudo python3 -m pip install --upgrade my-snaps`

**NOTE**:
* after install, run some tests per "Initial and Regression Testing"

---

## my-snaps
`my-snaps` can be used for simple snapshot maintenance. After running, it may look like this:

![my-snaps.png](https://github.com/joedefen/my-snaps/blob/main/images/my-snaps.png?raw=true)
<!--- ![my-snaps.png](images/my-snaps.png) -->

* In the header, the BTRFS partitions are shown with `df -h` info (showing Size, Used, Avail, Use%, and Mounted on); run df separately to remind you of the fields when needed.
* On your very first run, highlight each subvolume for which you wish snapshots, and press `s` to create one (use one of your standard labels)
* `r` replaces your eldest snapshot of the same label for each top-level subvolume that has any snapshots.
* `a` replaces a snapshot of the same label for each top-level subvolume that has any snapshots.
  * to describe snapshots, add a short label when prompted (e.g., "=Update").
* `d`: to remove highlighted subvolume (usually pick a snapshot); you cannot remove mounted subvolumes; if there are nested subvolumes, those are removed too.
* `u`: to get disk usage (this can take quite a while and is not perfect)
* `?`: to get help on all keys and navigation

**NOTE**: actions often require confirmation to ensure accidental keystrokes do not clobber your system.

**Non-interactive use**: `my-snaps` can be run non-interactively with these options:
* `-p` or `--print` dumps your top-level subvolumes and their snapshots
* `-s{N}` or `--add-snap-max={N}` adds a new snapshot for each subvolume with snapshots and removes the eldest until there are no more than `{N}`.
* `-l{label}` or `--label={label}` to set the label of the snapshots involved.
* `--cron={period}` adds an anacron job to add snapshots at the given period with appropriate defaulted `-s` and `-L` or you can specify those
  * job is stored in `/etc/cron.{period}/{period}.snaps` 
  * each time the job is run, its output goes to `/tmp/.my-snaps-{period}.txt`
  * removal of the job is done manually

---

## my-restore
`my-restore` is used to restore one or more of your snapshots. When launched, you see something like this:

![my-restore-p1.png](https://github.com/joedefen/my-snaps/blob/main/images/my-restore-p1.png?raw=true)
<!--- ![my-restore-p1.png](images/my-restore-p1.png) --->

Choose the desired BTRFS partition to mount on `/mnt` (after running `umount /mnt` if occupied).

Next you'll see a screen like this:

![my-restore-p2.png](https://github.com/joedefen/my-snaps/blob/main/images/my-restore-p2.png?raw=true)
<!--- ![my-restore-p2.png](images/my-restore-p2.png) --->

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
* install these tools per the instructions (a non-sudo install is OK)
* use `my-restore` to return to a working system and reboot and eventually clean up the reverted subvolumes on the installed system.

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

If you installed period snap scripts, ensure:
* tests with `sudo run-parts --debug -v /etc/cron.daily` (or whichever directory)
* run `ls -ltr /tmp/.my-snaps-*` to check that it was run recently by looking at timestamps.

---

## Best Practices for Using this Simple Snapshot Strategy
* `my-snaps` and `my-restore` support the most simple BTRFS snapshot strategy (for update protection and limited file recovery).  To guard against huge catastrophes, add complementary strategies such as these so you can quickly reinstall if needed:
  * keep your important document it the cloud (e.g., Google Drive)
  * keep critical configuration on github (e.g., using [How to Store Dotfiles - A Bare Git Repository | Atlassian Git Tutorial](https://www.atlassian.com/git/tutorials/dotfiles) or a dotfile manager).
  * create a list or script of post-install tasks to add/remove apps (save with your dotfiles or in the cloud)
* BTW, the above complementary strategies have even more value if you have multiple installs (e.g., a desktop, a 24/7 server, and a few laptops all running similarly with the same apps an basic config).
* For more comprehensive protection, consider `snapper`, `btrbk`, `btrfsmaintenance`, and google for others.