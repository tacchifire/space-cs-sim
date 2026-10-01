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
If the port is occupied, use `--port 0` to choose an available port.
By default, recordings are stored under `$XDG_DATA_HOME/cuberange/flatsat/web`, or `~/.local/share/cuberange/flatsat/web` if that variable is unset.
Choose a different directory with `--data-dir`.

```bash
python3 tools/flatsat.py serve --port 0 --data-dir "$HOME/.local/share/cuberange/flatsat-console"
```

Stop the server with Ctrl+C in its terminal.
An active USB operation finishes before the server exits; its duration is bounded by the recording form.

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

## Read the exercise catalog

The exercise page reads titles, prerequisites, durations and objective excerpts from the repository's bilingual README files.
The teaching order comes from `tools/syllabus.py`.
Open the procedure or copy its Make command to run an exercise in the repository's existing workflow.
The console does not mark an exercise as passed or start it when its page is viewed.

## Local access and validation

The HTTP listener binds only to `127.0.0.1`.
The server checks Host and Origin headers and requires its per-server token on requests that start jobs or import recordings.
Static assets are bundled in the repository; the console loads no CDN scripts, fonts or remote services.
Its USB operations are the existing shell queries and receive recording; it provides no RF transmit, firmware flashing or arbitrary command execution endpoint.

Run the offline tests without connecting a board.
The HTTP tests replace USB operations with a controlled adapter and cover accepted jobs, concurrent-job rejection, malformed requests, cross-origin requests and capture paths.

```bash
make flatsat-check
```
