# CubeRange local web console

*日本語版: [flatsat-web.ja.md](flatsat-web.ja.md)*

The local web console brings FlatSat USB inventory, sensor recording, saved logs and the simulation exercise catalog into one browser window.
It uses the existing FlatSat adapter and starts no Renode process.

## Start the console

Install the optional USB dependency in an environment outside the checkout, as described in [FlatSat USB diagnostics](flatsat.md).
Start the console from the repository root and open the URL printed in the terminal.

```bash
python3 tools/flatsat.py serve
# Equivalent Make target
make flatsat-web
```

The default URL is `http://127.0.0.1:8765/`.
To access the console from another device in the same Tailscale network, bind to this host's Tailscale IPv4 address.

```bash
python3 tools/flatsat.py serve --host "$(tailscale ip -4)"
```

Open the printed URL from a device allowed to reach this host by the tailnet's access rules.
Tailscale access rules control who can use the console; a device allowed to reach this address can read recordings and start the supported USB jobs.
The HTTP session token protects against cross-origin requests.
If the port is occupied, use `--port 0` to choose an available port.
By default, recordings are stored under `$XDG_DATA_HOME/cuberange/flatsat/web`, or `~/.local/share/cuberange/flatsat/web` if that variable is unset.
Choose a different directory with `--data-dir`.

```bash
python3 tools/flatsat.py serve --port 0 --data-dir "$HOME/.local/share/cuberange/flatsat-console"
```

Stop the server with Ctrl+C in its terminal.
An active USB operation finishes before the server exits; its duration is bounded by the recording form.

## Use the console on a phone

Connect the phone to the same tailnet and open the printed Tailscale URL.
On narrow screens, the menu stays at the bottom, forms use a single column and saved recordings are selected from a dropdown.
The graph adjusts to the screen width; wide tables scroll within their own area.
The selected recording and graph metric are restored after reloading in the same browser tab when session storage is available.
If the browser cannot copy an exercise command automatically, a dialog selects the command for manual copying; on a phone, long-press the selected text and choose Copy.

## Inspect and record a board

Choose a board by its USB serial number and check the permission indicator for each port.
The information action queries firmware, status and sensors through `Cat-Shell`.
The recording form accepts a duration, query interval, experiment label, note and optional sensor limits.
Durations are at most 60 seconds and query intervals are at least 0.02 seconds.
Only one USB job runs at a time; another start request is rejected while it runs.

The receive action records bytes without sending a radio data command.
An opened port with zero received bytes is displayed as an empty receive recording, not as evidence of telemetry reception.
Port permission errors remain visible in the job result.
The console cannot grant operating-system device permissions.

## Browse recordings

Completed recordings appear in the saved-log list.
Select a recording to inspect its metrics, valid and failed sample counts, graph and limit transitions.
The graph leaves gaps at failed samples and labels its timing as host query timing.
Download the original JSONL or open its standalone HTML report from the selected recording.

Import an existing JSONL file with the upload field to view earlier CLI experiments.
The server validates the file before storing a new copy and accepts at most 2 MiB.
It does not overwrite the source file or accept an arbitrary filesystem path from the browser.
Incomplete or failed recordings retain their status when imported.

## Monitor indoor changes

Open Indoor monitoring, choose the board and start a monitoring session while the board is resting in its usual position.
The default session lasts one hour and queries the sensors once per second; the duration can be set up to 24 hours.
The first 20 valid replies establish a fixed baseline using the median of each sensor component.
Monitoring begins after that baseline is ready.

The movement measurement is the length of the difference between the current X/Y/Z acceleration vector and its baseline vector.
This detects a change in the board's orientation even when the acceleration magnitude remains near 1 g.
Temperature, humidity and pressure are compared with their own initial baselines.
The initial change thresholds are configurable:

| Measurement | Change from baseline |
| --- | --- |
| Movement of the board | 80 mg |
| Temperature | 2 °C |
| Humidity | 10 percentage points |
| Pressure | 500 Pa |

By default, a change must reach or exceed its threshold on three consecutive valid replies before an event is saved.
The event remains active until three consecutive replies fall within 60 percent of the threshold, when a recovery event is saved.
An unavailable reply breaks the consecutive-reply count and does not count as recovery.
The baseline stays fixed for the session; starting a new session establishes a new baseline.

The monitor saves baseline, change, recovery and availability events immediately to JSONL, together with periodic status records every 60 seconds.
Change and recovery events retain the consecutive sensor values and USB replies used to confirm the transition.
The monitor does not save every poll as a continuous sensor recording.
The screen shows the latest readings and event history; saved logs and standalone reports show the retained events.
The total query count and the event count describe different things.

Closing the browser tab does not stop a session while the server remains running.
Use Stop monitoring to release the USB port; other console USB operations wait until that session ends.
A response timeout or device error ends the session and leaves its reason in the recording.
Stopping the server also stops the monitor, and a server restart does not resume a previous session automatically.
Recording stops at 16 MiB to bound disk usage.

These events describe changes at the board, not a direct determination that a person is present in the room.
Brief impacts between queries may be missed, and the configured change thresholds are observation settings, not calibrated safety limits.

## Read the exercise catalog

The exercise page reads titles, prerequisites, durations and objective excerpts from the repository's bilingual README files.
The teaching order comes from `tools/syllabus.py`.
Open the procedure or copy its Make command to run an exercise in the repository's existing workflow.
The console does not mark an exercise as passed or start it when its page is viewed.

## Local access and validation

The HTTP listener binds to `127.0.0.1` by default, or to the Tailscale IPv4 address selected with `--host`.
The allowed addresses are the literal `127.0.0.1` and IPv4 addresses in `100.64.0.0/10`; wildcard, LAN and hostname bindings are rejected.
For a Tailscale listener, the socket peer must also be `127.0.0.1` or in `100.64.0.0/10`; forwarding headers do not replace this check.
The server checks Host and Origin headers and requires its per-server token on requests that start jobs or import recordings.
Static assets are bundled in the repository; the console loads no CDN scripts, fonts or remote services.
Its USB operations are the existing shell queries and receive recording; it provides no RF transmit, firmware flashing or arbitrary command execution endpoint.

Run the offline tests without connecting a board.
The HTTP tests replace USB operations with a controlled adapter and cover accepted jobs, concurrent-job rejection, malformed requests, cross-origin requests and capture paths.

```bash
make flatsat-check
```
