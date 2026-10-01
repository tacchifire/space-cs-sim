# FlatSat USB diagnostics

*日本語版: [flatsat.ja.md](flatsat.ja.md)*

`tools/flatsat.py` provides USB inventory, receive-only recording, local shell queries, and continuous sensor capture for an owner-operated Electronic Cats FlatSat v1.0 RP2040 teaching board.
It runs separately from the Renode exercises; their STM32 firmware is not ported to the RP2040.

## Supported USB profile

The initial profile selects USB identity `1209:babc` and derives port roles from USB descriptors, rather than `ttyACM` numbers, following the pinned [Electronic Cats serial manager](https://github.com/ElectronicCats/flatsat-ground-station/blob/3a2d7b9500520015232cd9d6fc7b0d1916ee866c/modules/core/serial_manager.py).
On 2026-09-30, the connected board exposed these interfaces:

| USB interface | CLI role | Observed Linux path |
| --- | --- | --- |
| `Cat-Radio0` | `radio0` | `/dev/ttyACM0` |
| `Cat-Radio1` | `radio1` | `/dev/ttyACM1` |
| `Cat-Shell` | `shell` | `/dev/ttyACM2` |

Paths can change after reconnecting.
USB discovery and `Cat-Shell` queries succeeded on that date; the radio interfaces still require access permission and passive radio reception remains unverified.
`fw_version` reported `dev-f81af0b-dirty`, built `2026-08-11T20:38:22Z`; `status` reported `RadioManager LISTENING`, and `sensors` returned acceleration and environmental measurements.
One second of receive-only shell monitoring opened successfully and received zero unsolicited bytes; this does not establish telemetry reception.
The shell's `help` response did not expose a local CCSDS injection command.
The reference source revision `3a2d7b9` is a packet-format reference, not the firmware installed on this board; acceptance of that packet format by the board remains unverified.

## Setup and permissions

Run from the repository root.
The optional dependency is `pyserial==3.5`; the Renode simulation does not need it.
Keep the virtual environment outside the checkout, because repository validation scans source files recursively.

```bash
python3 -m venv ~/cuberange-flatsat-venv
. ~/cuberange-flatsat-venv/bin/activate
python3 -m pip install -r requirements-flatsat.txt
python3 tools/flatsat.py devices
```

`devices` reads descriptors without opening a serial port or sending a probe.
If it reports `permission required`, a Linux administrator can grant temporary access to these specific device paths:

```bash
sudo setfacl -m "u:$(id -un):rw" /dev/ttyACM0 /dev/ttyACM1 /dev/ttyACM2
python3 tools/flatsat.py devices
```

The ACL may disappear after unplugging the board; use the paths from a fresh `devices` result when granting access again.

## Capture and local information

Query the local shell, then record its unsolicited output for ten seconds.
`monitor` sends no serial data; the example selects the accessible shell interface.
Without `--port`, it opens all interfaces on one selected board and needs permission for all of them.

```bash
python3 tools/flatsat.py info --output /tmp/flatsat-info.jsonl
python3 tools/flatsat.py info --query help --output /tmp/flatsat-help.jsonl
python3 tools/flatsat.py monitor --port shell --duration 10 --output /tmp/flatsat-monitor.jsonl
```

`info` writes only `fw_version`, `status`, and `sensors` to `Cat-Shell` by default.
`--query` selects an individual command from those three or `help`, and can be repeated.
These local queries are listed in the pinned [firmware verification tool](https://github.com/ElectronicCats/flatsat-ground-station/blob/3a2d7b9500520015232cd9d6fc7b0d1916ee866c/modules/firmware/verify.py).
A timeout or unknown command is a failed query, not evidence of a firmware version.

For monitoring, `--port shell`, `--port radio0`, or `--port radio1` selects a role; an explicit device path is also accepted.
With multiple boards connected, select one using `--serial` and the board ID printed by `devices`.
Each JSONL capture stores UTC timestamps, session descriptors, and the exact received chunks as `raw_hex`; `info` also records transmitted query bytes.
USB read boundaries are not packet boundaries, so `monitor` does not automatically decode chunks.
Existing output files are never overwritten; choose a new path for each run.
Without `--output`, captures go under the checkout-specific `out_dir()/flatsat/` directory, or `$OUT/flatsat/` when `OUT` is set.

The Makefile provides `make flatsat-devices`, `make flatsat-monitor`, `make flatsat-info`, `make flatsat-watch`, `make flatsat-summary`, `make flatsat-report`, `make flatsat-compare`, `make flatsat-ping`, and `make flatsat-check`.
`FLATSAT_PYTHON` selects the interpreter (default `python3`); `FLATSAT_ARGS` forwards additional CLI options.
For `flatsat-summary` and `flatsat-report`, pass the capture path through `FLATSAT_ARGS`.
`make flatsat-check` runs the offline tests in `tests/pytest/test_flatsat*.py`.

## Continuous sensor capture

The observed shell returns sensor values when queried, while the one-second passive monitor received no unsolicited output.
Use `watch` to record changes over time: it opens one `Cat-Shell` connection and repeatedly sends only the `sensors` query.

```bash
python3 tools/flatsat.py watch --duration 10 --interval 1 --output /tmp/flatsat-watch.jsonl
python3 tools/flatsat.py summary /tmp/flatsat-watch.jsonl --json
python3 tools/flatsat.py report /tmp/flatsat-watch.jsonl --output /tmp/flatsat-report.html
```

The defaults are `--duration 10`, `--interval 1`, and `--timeout 2`, all in seconds.
The duration sets the query time budget; a blocking USB read can finish slightly later, at approximately 50 ms timeout granularity.
Slow replies do not trigger bursts of catch-up queries.
Choose a fresh output path for each run.
In a ten-second capture on 2026-09-30 (JST), all ten samples were valid, none failed, and the reported temperature ranged from 27.67 to 27.76 C.

The JSONL capture retains transmitted queries and exact received bytes, then adds a structured `sensor_sample` for each attempt.
`watch` requires a complete echoed `sensors` command line to identify the start of a fresh reply; bytes received before that echo remain in the raw log.
The observed firmware provides this echo.
A complete valid sample requires all six finite values with the units emitted by the shell:

| Measurement | Required unit |
| --- | --- |
| Acceleration X | `mg` |
| Acceleration Y | `mg` |
| Acceleration Z | `mg` |
| Temperature | `C` |
| Pressure | `Pa` |
| Relative humidity | `%` |

Here `mg` means one thousandth of standard gravity; the acceleration includes gravity.
Each sample's host timestamp marks the start of its query; it is not a measurement timestamp supplied by the sensor.
Keep those units when comparing recordings; for example, pressure in `Pa` differs from pressure in `hPa` by a factor of 100.

Missing values are JSON `null`, never zero.
A timeout, including one that leaves a partial reply, produces a failed sample and stops `watch`.
Identical shell queries carry no request ID, so a delayed reply cannot safely be assigned to a retry.
A reply that finishes at the silence boundary but lacks fields is a failed sample, and polling can continue.
Summaries use only complete valid samples and count failures separately.
If any sample fails, `watch` returns a nonzero exit status and retains the log for inspection.

`summary` and `report` read saved captures without opening USB.
The HTML report includes a metric selector and displays failed samples as gaps, so a missing measurement does not become a zero or a continuous line.
It uses no external resources and can be opened offline.

## Sensor limits and events

Set an inclusive range with repeatable `--limit metric:min:max` options.
Leave one bound empty for a one-sided range; at least one bound is required.
Each metric can have one range, and bounds must be finite numbers in ascending order.
The example ranges are experiment conditions chosen by the operator, not board operating limits.

```bash
python3 tools/flatsat.py watch --duration 3 --limit temperature_c:20:30 --limit humidity_percent::65 --label limits-demo --output /tmp/flatsat-limits.jsonl
python3 tools/flatsat.py alerts /tmp/flatsat-limits.jsonl --json
python3 tools/flatsat.py report /tmp/flatsat-limits.jsonl --output /tmp/flatsat-limits.html
python3 tools/flatsat.py alerts /tmp/flatsat-baseline.jsonl --limit temperature_c:20:30 --fail-on-alert
```

Metric names are `temperature_c`, `pressure_pa`, `humidity_percent`, `accel_x_mg`, `accel_y_mg`, `accel_z_mg`, and `acceleration_norm_mg`.
Use the units listed above; acceleration magnitude includes gravity.
An initial out-of-range value or a transition to another out-of-range state produces `triggered`; a return from an out-of-range state to the range produces `recovered`.
Unchanged states do not produce duplicate events.
A partial, invalid, or timed-out sample makes every configured metric `unknown`, producing `unavailable` when this state begins.
A subsequent valid in-range sample produces `resumed`, distinguishing resumed observation from an uninterrupted recovery.
The session retains the ranges, and each `alert` event retains the sample index, query-start timestamp, elapsed time, value, bounds, and transition.

`alerts` replays validated saved samples offline, using the recorded ranges or replacing all of them with explicit `--limit` options.
The HTML `report` includes the same replayed event timeline and supports the same range override.
Replay recomputes transitions from the samples rather than trusting recorded `alert` entries.
Without configured ranges, `watch` retains its existing behavior and the report omits the event section.
By default, exceeding a range is recorded while a successful capture exits with code `0`.
With `--fail-on-alert`, `watch` or `alerts` exits with code `3` if any out-of-range transition occurred; capture or data errors still exit with code `1`.
For gated offline replay, an empty recording, any failed sample, or incomplete recording also exits with code `1`.
`watch --fail-on-alert` requires at least one range.
Invalid command-line syntax exits with code `2`, and an interrupted capture exits with code `130`.
`make flatsat-alerts FLATSAT_ARGS=...` runs the offline replay.

## Compare two experiments

Record an experiment name with `--label` and its conditions with repeatable `--note` options.
The log and HTML report retain this context alongside the measured values.

```bash
python3 tools/flatsat.py watch --duration 3 --label baseline --note 'interval=1s' --output /tmp/flatsat-baseline.jsonl
python3 tools/flatsat.py watch --duration 3 --interval 0.1 --timeout 0.1 --label candidate --note 'interval=0.1s' --output /tmp/flatsat-candidate.jsonl
python3 tools/flatsat.py compare /tmp/flatsat-baseline.jsonl /tmp/flatsat-candidate.jsonl --output /tmp/flatsat-comparison.html
python3 tools/flatsat.py compare /tmp/flatsat-baseline.jsonl /tmp/flatsat-candidate.jsonl --json
```

`compare` works offline and reports the candidate mean minus the baseline mean for each metric.
Its HTML report overlays both runs on shared axes, preserving their individual spacing and gaps, with each run's time measured from its first recorded sample.
The sample counts can differ; the tool compares independent recordings rather than pairing their samples.
Board IDs are checked, and missing IDs or different boards are displayed explicitly.
A missing mean or an unrepresentable difference stays unavailable.
The difference describes the two logs; interpreting its cause requires the recorded experimental conditions.

`summary` also reports sample standard deviation, first and last values, their difference, and the observed range.
Standard deviation requires at least two valid samples; unavailable statistics retain `null` and an explanation.
The timing summary includes the observed host record rate, query processing time, and recording completion status.
Missing or nonincreasing host timings make the record rate unavailable rather than implying a valid rate.
Host record rate is not the sensor's internal acquisition rate.

For a complete synchronized six-field sensor reply, the observed firmware's final blank line ends the query immediately.
Other replies still use the quiet-period check; incomplete replies cannot pass just because they contain a blank line.
The default interval remains one second.
On 2026-10-01 (JST), a three-second run at a 0.1-second interval recorded 28 valid replies with no failed samples.
That log's host record rate was approximately 9.986 Hz, and mean query processing time was approximately 5.718 ms.

## An experiment with OODA

Use a short capture to test a concrete question, such as how the three acceleration axes change when the board's orientation changes.
The **OODA loop** organizes the work as observation, interpretation, a decision, and an action:

| Step | Practical action |
| --- | --- |
| Observe | Record a baseline with `watch`, noting the board's position and the capture path. |
| Orient | Use `summary` and `report` to review values, changes, and failed samples. |
| Decide | Choose one condition to change and the measurement to compare with the baseline. |
| Act | Change that condition, record another short capture, and compare the two records. |

The operator makes the change; these commands record and review the experiment without scheduling future actions.
Repeated identical readings alone do not prove a frozen sensor, since reported values can be quantized.
Check sample failures and the original reply before interpreting a flat line as a hardware problem.

## Offline packet inspection

PING currently supports only packet construction and inspection; it does not open USB or transmit.

```bash
python3 tools/flatsat.py ping --dry-run
python3 tools/flatsat.py decode 1820c0010006000000001022d9
```

The sample is a synthetic plaintext PING with APID `0x020`, sequence `1`, timestamp `0`, opcode `0x10`, and a valid CRC.
The profile uses a six-byte CCSDS primary header, a four-byte mission elapsed time when the secondary-header flag is set, and a two-byte packet CRC, following the pinned [Electronic Cats packet codec](https://github.com/ElectronicCats/flatsat-ground-station/blob/3a2d7b9500520015232cd9d6fc7b0d1916ee866c/modules/core/ccsds.py).
The decoder reports header fields, payload bytes, and CRC validity; it does not infer sensor layout or decrypt payloads.

This profile differs from CubeRange's PUS and transfer-frame format, and from the two-CDC Pwnsat profile (`239a:cafe`) with `AA55` USB framing.
Live PING is disabled because the board's local packet injection path has not been verified; radio transmit interfaces can emit RF.
The tool does not flash firmware or connect exercises to hardware.
See [SAFE_USE.md](../SAFE_USE.md) for the teaching-board scope.
