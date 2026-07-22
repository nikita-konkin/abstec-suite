# absolTEC on Windows XP via dockur/windows (KVM)

Alternative to the wine runner: `absolTEC.exe` executes inside a real
Windows XP Professional guest running in a [dockur/windows](https://github.com/dockur/windows)
KVM container. The binary is a 32-bit PE targeting OS 5.1, i.e. XP is its
native platform.

## How it works

```
run_absoltec.py --runner dockur          Windows XP guest (abstec-xp)
        |                                        |
        |  writes job folder                     |  watcher.bat polls W:\jobs
        v                                        v
  <jobs dir>/<job-id>/            \\host.lan\Data\jobs\<job-id>\
      absolTEC.dia     ---------->   copied to C:\absoltec\work\sN
      job.bat          ---------->   executed, stdout -> job.log
      job.ready                      results xcopy'd to W:\out\_stage\<job-id>
                       <----------   exit code written to job.done
```

- The host runner tails `job.log` live (same progress parsing as the wine
  runner) and reads the exit code from `job.done`.
- On timeout the host writes `job.kill`; the guest watcher then
  `taskkill`s that slot's absolTEC copy and reports exit code 124.
- Successful job folders are deleted; failed ones are kept for inspection.
- Guest side is plain XP-era batch (no SSH/WinRM needed): `dockur/oem/install.bat`
  provisions `watcher.bat` as an autostart entry and enables RDP.
- Results are staged per job under `<out>/_stage/<job-id>/` and only then moved
  to `<out>/<year>/<doy>/<site>/`. absolTEC always names its result folder after
  the 4-character site prefix, so without per-job staging two stations sharing a
  prefix (`kudi0080` / `kudi0081`) overwrite each other.

## Running jobs in parallel

One station at a time leaves the guest mostly idle — much of each job is SMB
polling, not computation. The watcher can run several jobs concurrently, one per
*slot*. Each slot has its own workdir (`C:\absoltec\work\sN`) and its own copy of
the executable (`absolTEC_sN.exe`), because concurrent runs must not share
`absolTEC.dia`, the result folder, or a `taskkill`.

1. Set the slot count in the shared jobs folder on the host:

   ```bash
   echo 4 > "$ABSTEC_DOCKUR_JOBS_PATH_HOST/_slots.cfg"
   ```

2. Give the guest enough CPU (roughly one core per concurrent job) in `.env`:

   ```bash
   ABSTEC_XP_CPU_CORES=4
   ABSTEC_XP_RAM_SIZE=4G
   ```

3. Restart the guest so the watcher re-reads both:
   `docker compose -f docker-compose.dockur.yml restart abstec-xp`

4. Run with `--jobs 4` (or set "Parallel Jobs" in the ict-hub GUI).

The watcher publishes what it actually supports in `<jobs dir>/_watcher.status`;
the host reads it at startup and warns if `--jobs` exceeds the slot count, or if
the guest is still running the older serial watcher. Jobs beyond the slot count
are not lost — they simply queue.

## Run manifest and resuming

Every station's outcome is appended to `<output-dir>/_manifest.csv` (override
with `--manifest`, disable with `--no-manifest`):

```
finished_at,year,day_of_year,site,status,reason,duration_seconds
2026-07-22T12:41:55,2025,008,ktiv,ok,,12.4
2026-07-22T12:42:08,2025,008,kudi,failed-runtime,RuntimeError: absolTEC exited with code 64...,3.1
```

A resumed run reads it and skips stations recorded as `ok` or `skipped-existing`.
Failures and bad-input skips are **not** treated as final, so re-exported data
gets another chance. This is stricter than `--skip-existing`, which infers state
from output folders and therefore cannot tell "produced no output" apart from
"never attempted" — those stations are retried on every resume forever.

`analyze_manifest.py` summarises a finished run:

```bash
python analyze_manifest.py /data/out/_manifest.csv                    # status + failure breakdown
python analyze_manifest.py /data/out/_manifest.csv --dat-path /data/in  # test strict validation
```

The second form is worth running once. `--strict-dat-validation` rejects stations
whose `.dat` rows cannot be parsed in Python, on the theory that those are what
make absolTEC abort with `severe (64): input conversion error`. That theory has
never been checked against real failures. The script compares the stations strict
validation would reject against the ones that actually crashed and prints recall,
precision, and a verdict — enough to either enable the flag by default or drop
the idea and look elsewhere.

## Housekeeping

Failed job folders are kept for inspection and staged output is removed only
after a successful hand-off, so both accumulate when runs die. They are not free:
every guest slot re-enumerates the jobs directory over SMB on each poll, so a
large pile slows the whole queue. Before a dockur batch starts, folders older
than `--job-retention-hours` (default 48, `0` disables) are pruned.

> **Updating the watcher on an existing guest.** `install.bat` only runs once,
> right after XP is installed, so editing `dockur/oem/watcher.bat` does not reach
> a guest that is already provisioned. The installed autostart entry is now a
> bootstrap that copies `<jobs dir>/watcher.bat` over the local copy at every
> logon, so to update an existing guest: copy `dockur/oem/watcher.bat` into the
> shared jobs folder and restart the guest. A guest provisioned before this
> change needs the bootstrap installed once by hand (RDP in on port 3390 and
> re-run `C:\OEM\install.bat`), or just recreate the guest.

## Requirements

- A host that can expose `/dev/kvm` to containers: native Linux (bare metal
  or with nested virtualization), or Windows via Docker Desktop + WSL2 —
  see "Host setup (KVM)" below for both.
- A Windows XP Professional license you are entitled to use.
- `ABSTEC_INPUT_DATA_PATH_HOST` / `ABSTEC_OUTPUT_DATA_PATH_HOST` set in `.env`
  (same variables the wine compose file uses).

## Host setup (KVM)

dockur/windows runs QEMU with hardware acceleration and refuses to start
without `/dev/kvm` inside the container (`abstec-xp` exits almost
immediately, typically with status 255). How `/dev/kvm` appears differs
per host OS.

### Ubuntu / native Linux

1. Enable VT-x (Intel) / AMD-V (SVM) in the BIOS/UEFI if not already on.
2. Install Docker Engine + the compose plugin
   (<https://docs.docker.com/engine/install/ubuntu/>).
3. Verify KVM:

   ```sh
   sudo apt install -y cpu-checker
   kvm-ok            # expect: "KVM acceleration can be used"
   ls -l /dev/kvm
   ```

   On bare metal the `kvm_intel` / `kvm_amd` module loads automatically at
   boot. If `/dev/kvm` exists but the container gets "permission denied",
   add your user to the `kvm` group (`sudo usermod -aG kvm $USER`,
   re-login) or run compose with sudo.
4. If Ubuntu itself is a VM, enable nested virtualization in the outer
   hypervisor first — without it `/dev/kvm` never appears.
5. Set the `.env` host paths in Linux form (e.g.
   `ABSTEC_INPUT_DATA_PATH_HOST=/srv/rinex/out`) and continue with
   "First-time bring-up".

### Windows (Docker Desktop + WSL2)

Works, but KVM inside the docker-desktop WSL2 distro is **not** enabled out
of the box:

1. Docker Desktop must use the WSL2 backend (Settings → General), with
   virtualization enabled in the BIOS. Nested virtualization for WSL2 is on
   by default on Windows 11; if it was disabled, set
   `nestedVirtualization=true` under `[wsl2]` in `%USERPROFILE%\.wslconfig`.
2. The WSL2 kernel ships KVM as a module but never loads it. Load it once
   (PowerShell, pick the module matching your CPU):

   ```powershell
   wsl -d docker-desktop -u root modprobe kvm_amd     # AMD
   wsl -d docker-desktop -u root modprobe kvm_intel   # Intel
   wsl -d docker-desktop ls -l /dev/kvm               # must exist now
   ```

3. This does **not** survive `wsl --shutdown`, a Docker Desktop restart, or
   a reboot: `/dev/kvm` disappears, `abstec-xp` shows `Exited (255)` and
   cannot be started until the module is reloaded. To make it permanent,
   add a boot command to `/etc/wsl.conf` inside the docker-desktop distro
   (use `kvm_intel` on Intel), then restart Docker Desktop once:

   ```powershell
   wsl -d docker-desktop -u root sh -c 'printf "[boot]\ncommand = \"modprobe kvm_amd\"\n" >> /etc/wsl.conf'
   ```

4. Host paths in `.env` use Windows form (e.g.
   `ABSTEC_INPUT_DATA_PATH_HOST=N:\RINEX\out`); Docker Desktop translates
   the bind mounts. Watch for typos here — the guest happily writes into
   whatever folder is mounted as `/shared/out`, so a misspelled path means
   results silently land elsewhere.

## First-time bring-up

```sh
docker compose -f docker-compose.dockur.yml up -d abstec-xp
```

1. Open `http://<host>:8006` and watch the unattended XP installation
   (roughly 10–20 minutes). Do not interrupt the first boot.
2. After installation, `install.bat` runs automatically and starts the
   watcher; a console window titled `abstec-watcher` should appear showing
   `share \\host.lan\Data mapped to W:` and `watching W:\jobs`.
3. Smoke test with a dry run, then a real single station:

```sh
docker compose -f docker-compose.dockur.yml run --rm \
  -e DRY_RUN=0 -e DAY_OF_YEAR=1 -e SITE=ozer0010 abstec-dockur
```

## Day-to-day use

Same knobs as the wine runner (`YEAR`, `DAYS`, `DAY_OF_YEAR`, `SITE`,
`TIME_STEP_HOURS`, `EXECUTION_TIMEOUT_SECONDS`), just a different service:

```sh
DRY_RUN=0 DAYS=001-031 docker compose -f docker-compose.dockur.yml run --rm abstec-dockur
```

Or without docker on the host side (any machine that can see the shared
folders):

```sh
python run_absoltec.py --runner dockur \
  --workdir TayAbsTEC_24.04.17 \
  --dat-path /path/to/in --output-dir /path/to/out \
  --dockur-jobs-dir ./dockur/jobs \
  --year 2026 --day-of-year 1 --site ozer0010
```

`--output-dir` must point at the host folder that is mounted to
`/shared/out` (the guest copies results there itself); the host then only
renames/organizes station folders.

### Compose path overrides

All host paths are overridable via env (defaults in parentheses):

```sh
ABSTEC_INPUT_DATA_PATH_HOST=...          # DAT input, guest W:\in (required)
ABSTEC_OUTPUT_DATA_PATH_HOST=...         # results, guest W:\out (required)
ABSTEC_DOCKUR_JOBS_PATH_HOST=...         # job queue, guest W:\jobs (./dockur/jobs)
ABSTEC_DOCKUR_STORAGE_PATH_HOST=...      # XP disk image, ~16G (./dockur/storage)
```

### From the ict-hub GUI

The AbsTEC Suite page in ict-hub has a **Runner** dropdown with a
`dockur (Windows XP VM)` option. To enable it, set in ict-hub's `.env`:

```sh
# host path of this repo's dockur/jobs folder (shared with the XP VM)
ABSTEC_DOCKUR_JOBS_PATH_HOST=/path/to/abstec-suite/dockur/jobs
# optional, defaults to W:\in\
# ABSTEC_DOCKUR_GUEST_DAT_PATH=W:\in\
```

and make sure `TECSUITE_OUT_DAT_DATA_PATH_HOST` / `ABSTEC_OUTPUT_DATA_PATH_HOST`
in ict-hub point at the same host folders the XP VM mounts as `/shared/in` and
`/shared/out`. The hub then mounts the jobs folder into the launched
abstec-suite container and passes `--runner dockur --dockur-jobs-dir /data/jobs`
automatically.

When a dockur job is submitted, the hub also checks the `abstec-xp` container
(name configurable via `ABSTEC_DOCKUR_VM_CONTAINER`) and starts it if it is
stopped — e.g. after a Docker Desktop restart. Note that a restart also drops
`/dev/kvm` unless `kvm_amd`/`kvm_intel` loading is persisted (see "Host setup
(KVM)" above), in which case the VM cannot start and the job fails with the
Docker error message. The container
must already exist (created once via the compose command above); the hub never
creates it.

## Notes and troubleshooting

- **Guest dat path**: `absolTEC.dia` line 1 is written as `W:\in\` by
  default (`--dockur-guest-dat-path` / `DOCKUR_GUEST_DAT_PATH` to override).
- **App updates**: the guest syncs `\\host.lan\Data\app` (the mounted
  `TayAbsTEC_24.04.17` folder) into `C:\absoltec\work` at watcher startup,
  so restart the container (`docker restart abstec-xp`) after replacing the exe.
- **RDP debugging**: `mstsc`/Remmina to `<host>:3390`, user `Docker`
  (dockur default). The watcher console is visible on the auto-logged-on
  desktop and in the `:8006` web viewer.
- **Job stuck?** Look into `dockur/jobs/<job-id>/` — `job.running` present
  but no `job.done` means the guest is still working (or rebooted mid-job;
  the watcher clears stale `job.running` markers at startup and retries).
- **Share not mapping**: older dockur/windows images used `/data` instead
  of `/shared` as the container mount point; if the guest shows an empty
  `\\host.lan\Data`, update the image or adjust the volume targets.
- Jobs run strictly sequentially in the guest — same semantics as the
  wine batch loop.
