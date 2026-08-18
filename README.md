# maniMon — a workstation monitor that keeps the numbers

[![License: AGPL v3](https://img.shields.io/badge/license-AGPL--3.0-blue.svg)](LICENSE)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.21987162.svg)](https://doi.org/10.5281/zenodo.21987162)

Two docked GTK panels and a headless recorder for a Linux workstation that runs
long jobs. It reads the sensors your machine already has — including several
that nothing else surfaces — stores the history, and tells you what each run
actually cost.

It exists because the usual answer to *"how hot did the GPU get during last
night's run, and what did it cost in watt-hours"* is that nobody kept the data.
Sparklines in a system monitor are ring buffers in a process: restart the
process and the history is gone.

## What it reads that most monitors do not

- **amdgpu `gpu_metrics`** — the binary v1_3 struct in sysfs, giving the three
  VR temperatures (GFX, SOC, MEM), the throttle status word, and average vs
  current clocks. None of these appear in hwmon. Offsets in `health.py` were
  verified against live bytes on an RDNA 3 card, not taken from a header.
- **The BMC**, via `ipmitool` — chassis fans, board temperatures, per-DIMM
  temperature and power, and the rails. On a server board that is 30+ sensors.
- **SMART**, honestly — see [Honesty about units](#honesty-about-units).
- **The DIMM inventory**, from DMI — which channels are populated, and so how
  much of your memory bandwidth you are actually able to use. On a 12-channel
  controller with 4 slots filled, that is the single most useful number on the
  machine and nothing else tells you.
- **ECC counters**, PSI pressure, RAPL package power, drive temperatures.

## What it does with them

A SQLite store on your home filesystem, three resolutions folded by age:

| res | period | kept for | rows/day |
|-----|--------|----------|---------:|
| `r`   | 10 s  | 48 h     | 8 640 |
| `1m`  | 1 min | 30 d     | 1 440 |
| `10m` | 10 min| forever  |   144 |

Roughly 400 MB/year, with retention enforced on every fold. Rates average when
folded; temperatures and power take the **maximum**, because averaging a
thermal peak away defeats the purpose of keeping history.

```
manimon report          # p95, dated peaks, kWh, time-over-threshold
manimon runs            # what each job cost
manimon doctor          # what is missing, and the command that fixes it
manimon config          # resolved configuration and where each part came from
```

Every subcommand is available as `python3 -m manimon <cmd>` straight from a
checkout, with no install step — see [Install](#install).

## Per-run accounting, and what it refuses to claim

`runs.py` tracks every job it recognises and separates two kinds of number,
because conflating them would be dishonest:

- **Exclusive** — CPU-seconds, peak RSS, peak threads, wall time. Measured from
  the process's own counters, so they are genuinely that run's, even when runs
  overlap.
- **Shared** — GPU power and energy, junction temperature, package watts.
  Machine-wide, sampled over the run's window, and **never divided up**.

One GPU feeding three jobs draws one board power; splitting it three ways would
invent a number no sensor measured. So shared figures are reported alongside
`concurrent` — the peak overlap during the window — and the report says
`alone` or `+2` rather than pretending to attribute.

## Honesty about units

The SMART code will refuse to convert a counter whose unit it cannot establish.
Attribute 241 is vendor-defined: drives count 512 B LBAs, 32 MiB chunks or
whole GiB under the same ID, and some report a unit-free count while still
naming the attribute `Total_LBAs_Written`. Read one such drive as LBAs and you
get "3 MB written in 3420 power-on hours", which the filesystem journal alone
would exceed.

So the sampler publishes the raw counter and the attribute name, and derives
bytes only when the unit is known, or when a per-model override records the
evidence. The panel shows `5,975?` with a question mark when the unit is
unknown, and `~6.4 TB` with a tilde when it was inferred rather than
established — never a confident wrong number.

Where a unit can be settled, it is settled by measurement rather than
argument. The override in `manimon/sensors/daemon.py` for one drive records exactly that:
write a known 8 GiB, wait for a SMART refresh, watch the counter advance by
8. That excludes 512-byte LBAs by a factor of two million, 32 MiB chunks by a
factor of 32, and gigabytes by 590 MB against a measured 8 GiB write. Once
measured, the figure stops being hedged.

Attribute 194 gets the same treatment: its raw field packs
`current | min<<16 | max<<32` on most drives, which is how monitors end up
reporting a disk at 210 billion degrees.

## Privilege

The panels never run as root. One module (`manimon/sensors/daemon.py`) runs as
root on a 30-second timer and writes world-readable JSON into a tmpfs
directory; the panels read files. The whole privileged surface is one auditable
file, and `manimon/sensors/published.py` is the only thing that reads its
output.

## Install

```bash
git clone https://github.com/manishthatte/maniMon
cd maniMon

python3 -m manimon doctor                                    # what is present, what is not
python3 -m manimon config --sample > ~/.config/manimon/config.toml   # optional
bash packaging/install_user_services.sh                      # recorder + watchdog, no sudo
sudo bash packaging/install_system_sensors.sh                # BMC / SMART / DIMM sampler
python3 -m manimon panels start                              # the panels
```

No install step is needed: the package sits at the top of the checkout, so
`python3 -m manimon` works immediately on a system Python with PEP 668 in
force, against your distribution's own PyGObject. If you would rather have a
`manimon` on `$PATH`:

```bash
pipx install .          # or: pip install --user .
manimon doctor
```

Nothing is required. With no config at all it still shows CPU, memory, GPU,
disks, network, sensors and the store — the job, repo, backup and campaign
sections simply stay hidden, because a section with nothing behind it is worse
than no section.

Requirements: Python 3.9+ (3.11+ for TOML config), GTK 3 via PyGObject for the
panels. `ipmitool`, `smartmontools`, `nvme-cli` and `dmidecode` are each
optional — whatever is missing is reported as missing rather than shown as
zero. `manimon doctor` says what is available and what each missing piece
would have given you.

## Theme

Light and dark palettes, both measured rather than eyeballed. `palette.py` is a
runnable self-test: it prints every contrast ratio against WCAG and fails if
one regresses. The categorical colours are checked under simulated
protanopia, deuteranopia and tritanopia — the obvious "just darken the dark
theme" light palette measured ΔE 3.0 under deuteranopia, i.e. indistinguishable,
and was rejected.

```bash
manimon palette             # contrast + colour-vision report
manimon preview out.png     # render a panel mockup to PNG, no X server needed
```

## Layout

```
manimon/
├── cli.py            one entry point:  manimon <command>
├── config.py         site configuration, merged from TOML
├── doctor.py         what is missing, and the command that fixes it
├── util.py           file reads that cannot raise, and the formatters
├── collect/          the readers, one module per subsystem
│   ├── cpu · memory · disk · net · gpu
│   ├── process · jobs · system · attention
│   └── __init__      the facade: one tick, three refresh tiers
├── sensors/          health · daemon (root) · published (unprivileged)
├── store/            metrics (the time series) · runs (per-run accounting)
└── ui/               palette · widgets · window · launcher
    └── sections/     one module per panel section
```

Two rules the tests enforce, because both were violated before:

- **No module over 700 lines.** The collection layer was a single 1,870-line
  file doing ten unrelated jobs.
- **Each panel section owns both halves of its job** — the widgets it creates
  and the code that fills them. Those used to sit 150 lines apart, which is how
  a widget can be created and never updated without anything noticing.

Nothing imports GTK except the panel modules themselves, so `manimon report`
works over SSH on a machine with no display. That is a test too.

## Tests

```bash
python3 -m unittest discover -s tests -t .     # no dependencies
python3 -m pytest                              # if you prefer pytest
```

The suite is standard-library `unittest` on purpose: this tool has no runtime
dependencies and installs nothing, and a test suite that needs a pip install
on a PEP 668 system is a test suite that does not get run.

Every case is a regression, not a hypothetical. The famous ones:

- a disk reporting **210,454,380,576 °C**, because attribute 194 packs three
  values into one integer
- **3 MB written** across 3,420 power-on hours, because attribute 241's unit is
  vendor-defined
- a recorder writing **precisely nothing** for a whole session, because sqlite3
  binds a connection to its creating thread and the error was caught into a
  field nobody read. That test asserts on the row count, not on the absence of
  an exception — the old code did not crash, it silently stored nothing.

## Status

Written for and running on one machine: a dual-socket-class AMD EPYC
workstation with Radeon Pro GPUs under Debian 13, GNOME on Wayland with the
panels under XWayland. The sensor readers degrade gracefully elsewhere, but
they have not been tested widely. Reports from other hardware are welcome —
especially the `gpu_metrics` struct version on other AMD cards.

## Licence

AGPL-3.0-or-later. See [LICENSE](LICENSE).

A commercial licence is available for use where the AGPL is unsuitable —
contact manish@manitlab.org.

© Manish Jagdish Thatte
