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
      absolTEC.dia     ---------->   copied to C:\absoltec\work
      job.bat          ---------->   executed, stdout -> job.log
      job.ready                      results xcopy'd to W:\out\<year>
                       <----------   exit code written to job.done
```

- The host runner tails `job.log` live (same progress parsing as the wine
  runner) and reads the exit code from `job.done`.
- On timeout the host writes `job.kill`; the guest watcher then
  `taskkill`s absolTEC.exe and reports exit code 124.
- Successful job folders are deleted; failed ones are kept for inspection.
- Guest side is plain XP-era batch (no SSH/WinRM needed): `dockur/oem/install.bat`
  provisions `watcher.bat` as an autostart entry and enables RDP.

## Requirements

- Linux host with `/dev/kvm` (bare metal or nested virtualization enabled).
- A Windows XP Professional license you are entitled to use.
- `ABSTEC_INPUT_DATA_PATH_HOST` / `ABSTEC_OUTPUT_DATA_PATH_HOST` set in `.env`
  (same variables the wine compose file uses).

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
