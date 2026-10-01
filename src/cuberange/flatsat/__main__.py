"""USB diagnostics and recorded FlatSat sensor experiments."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from contextlib import ExitStack
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from ..paths import out_dir
from .alerts import AlertTracker, evaluate_capture, parse_limit, validate_limits
from .protocol import build_ping, decode_packet
from .telemetry import METRICS, load_capture, parse_sensor_response
from .usb import FlatSatError, QUERY_COMMANDS, SerialSession, discover_ports, select_ports

MAX_QUERY_REPLY = 65536


def positive_seconds(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("duration must be a positive finite number")
    return number


def web_port(value: str) -> int:
    number = int(value)
    if not 0 <= number <= 65535:
        raise argparse.ArgumentTypeError("port must be between 0 and 65535")
    return number


def _limit_argument(value: str):
    try:
        return parse_limit(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _default_capture(command: str) -> Path:
    return out_dir() / "flatsat" / f"{command}-{time.time_ns()}.jsonl"


class Capture:
    """Append raw events immediately so interruptions retain all bytes already read."""
    def __init__(self, path: Path):
        self.path = path
        self._file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("x", encoding="utf-8")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self._file is not None:
            try:
                if exc_value is not None:
                    self.event("error", message=str(exc_value))
            finally:
                self._file.close()

    def event(self, event: str, **fields) -> None:
        record = {"time_utc": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        self._file.write(json.dumps(record, ensure_ascii=True) + "\n")
        self._file.flush()


def _receive(link: SerialSession, capture: Capture, *, command: str | None = None) -> bytes:
    chunk = link.read()
    if chunk:
        capture.event("rx", port=link.port.path, role=link.port.role,
                      command=command, raw_hex=chunk.hex())
    return chunk


def _terminal_text(text: str) -> str:
    # Keep line breaks readable while escaping any terminal control sequences.
    return "".join(char if char.isprintable() or char in "\n\t"
                   else "" if char == "\r" else f"\\u{ord(char):04x}"
                   for char in text)


def run_monitor(ports, duration: float, output: Path) -> int:
    received = 0
    with Capture(output) as capture, ExitStack() as stack:
        capture.event("session", mode="monitor", ports=[asdict(p) for p in ports])
        links = [stack.enter_context(SerialSession(p)) for p in ports]
        print(f"Capturing USB RX for {duration:g}s -> {output}", flush=True)
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            for link in links:
                if time.monotonic() >= deadline:
                    break
                chunk = _receive(link, capture)
                if chunk:
                    received += len(chunk)
                    # repr escapes terminal control bytes received from the board.
                    print(f"[{link.port.role}] {chunk.decode('utf-8', errors='replace')!r}", flush=True)
        capture.event("end", received_bytes=received)
    print(f"Captured {received} bytes -> {output}")
    return 0


def _drain(link, capture, deadline: float) -> None:
    # Keep all queued/banner bytes as evidence without assigning them to a query.
    while time.monotonic() < deadline:
        _receive(link, capture)


def _reply_text(command: str, text: str) -> tuple[str, bool]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.splitlines()
    echoes = [index for index, line in enumerate(lines) if line.strip() == command]
    useful = "\n".join(lines[echoes[-1] + 1:]).strip() if echoes else normalized.strip()
    return useful, bool(echoes)


def _sensor_reply(text: str) -> dict:
    useful, echoed = _reply_text("sensors", text)
    parsed = parse_sensor_response(useful)
    if not echoed:
        parsed["status"] = "invalid"
        parsed["errors"].append("missing complete sensors command echo; reply is not synchronized")
    if "unknown command" in useful.lower():
        parsed["status"] = "invalid"
        parsed["errors"].append("shell rejected the sensors command")
    return parsed


def _query(link, capture, command: str, timeout: float) -> dict:
    data = (command + "\r\n").encode("ascii")
    started = time.monotonic()
    capture.event("tx_attempt", port=link.port.path, role=link.port.role,
                  command=command, raw_hex=data.hex())
    link.write(data)
    capture.event("tx", port=link.port.path, role=link.port.role,
                  command=command, raw_hex=data.hex())
    response = bytearray()
    deadline = started + timeout
    last_rx = None
    completion = "timeout"
    while time.monotonic() < deadline:
        chunk = _receive(link, capture, command=command)
        now = time.monotonic()
        if chunk:
            response.extend(chunk)
            last_rx = now
            if len(response) > MAX_QUERY_REPLY:
                raise FlatSatError(f"Reply to {command} exceeded {MAX_QUERY_REPLY} bytes")
            if command == "sensors":
                text = response.decode("utf-8", errors="replace")
                normalized = text.replace("\r\n", "\n").replace("\r", "\n")
                # The observed shell closes its six-field reply with a blank line.
                # Require both that terminator and a synchronized, valid body;
                # a lone blank line or partially received fields cannot finish it.
                if normalized.endswith("\n\n") and _sensor_reply(text)["status"] == "ok":
                    completion = "complete"
                    break
        elif last_rx is not None and now - last_rx >= 0.2:
            completion = "quiet"
            break
    text = response.decode("utf-8", errors="replace")
    useful, _ = _reply_text(command, text)
    if useful.startswith(command + "\n") or useful == command:
        useful = useful[len(command):].strip()
    if command == "sensors":
        parsed = _sensor_reply(text)
        if completion == "timeout":
            parsed["status"] = "timeout"
        answered = parsed["status"] == "ok"
    else:
        parsed = None
        markers = {"fw_version": ("FW:", "Git:"),
                   "status": ("RadioManager State:",),
                   "help": ("Flat-Sat Shell", "sensors", "fw_version")}
        answered = (completion == "quiet" and "unknown command" not in useful.lower()
                    and all(marker in useful for marker in markers[command]))
    return {"text": text, "useful": useful, "answered": answered,
            "completion": completion, "response_ms": (time.monotonic() - started) * 1000,
            "parsed": parsed}


def run_info(port, commands, timeout: float, output: Path) -> int:
    if port.role != "shell":
        raise FlatSatError("info requires the Cat-Shell interface; choose --port shell")
    success = True
    with Capture(output) as capture, ExitStack() as stack:
        capture.event("session", mode="info", ports=[asdict(port)])
        link = stack.enter_context(SerialSession(port))
        _drain(link, capture, time.monotonic() + 0.2)
        for command in commands:
            result = _query(link, capture, command, timeout)
            if not result["answered"]:
                success = False
                print(f"[{command}] no complete recognized response ({result['completion']})", file=sys.stderr)
            else:
                print(f"[{command}]\n{_terminal_text(result['useful'])}")
            capture.event("query_result", command=command, **{
                key: result[key] for key in ("answered", "text", "completion", "response_ms")})
        capture.event("end", success=success)
    print(f"USB query capture -> {output}")
    return 0 if success else 1


def run_watch(port, duration: float, interval: float, timeout: float, output: Path,
              *, label: str | None = None, notes: list[str] | None = None,
              limits=None, fail_on_alert: bool = False) -> int:
    if port.role != "shell":
        raise FlatSatError("watch requires the Cat-Shell interface; choose --port shell")
    limits = validate_limits(limits or [])
    if fail_on_alert and not limits:
        raise FlatSatError("--fail-on-alert requires at least one --limit")
    tracker = AlertTracker(limits)
    trigger_count = 0
    count = valid = 0
    reason = "duration_complete"
    with Capture(output) as capture, ExitStack() as stack:
        capture.event("session", mode="watch", ports=[asdict(port)],
                      duration_s=duration, interval_s=interval, timeout_s=timeout,
                      label=label, notes=notes or [],
                      limits=[limit.as_dict() for limit in limits],
                      timestamp_basis="host query start; not device acquisition time")
        link = stack.enter_context(SerialSession(port))
        start = time.monotonic()
        deadline = start + duration
        _drain(link, capture, min(deadline, start + 0.2))
        next_query = time.monotonic()
        print(f"Polling Cat-Shell sensors for {duration:g}s -> {output}", flush=True)
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now < next_query:
                time.sleep(min(next_query - now, deadline - now, 0.05))
                continue
            elapsed = now - start
            requested_at = datetime.now(timezone.utc).isoformat()
            result = _query(link, capture, "sensors", min(timeout, deadline - now))
            count += 1
            parsed = result["parsed"]
            valid += parsed["status"] == "ok"
            capture.event("sensor_sample", time_utc=requested_at, index=count,
                          elapsed_s=elapsed, response_ms=result["response_ms"],
                          completion=result["completion"], **parsed)
            for event in tracker.update({**parsed, "completion": result["completion"]}):
                trigger_count += event["transition"] == "triggered"
                capture.event("alert", time_utc=requested_at, index=count,
                              elapsed_s=elapsed, **event)
                print(f"[{count}] limit {event['metric']}: {event['transition']} "
                      f"({event['state']}, value={event['value']})", flush=True)
            values = parsed["values"]
            if parsed["status"] == "ok":
                print(f"[{count}] {elapsed:.2f}s  {values['temperature_c']:.3f} C  "
                      f"{values['pressure_pa']:g} Pa  {values['humidity_percent']:g}%  "
                      f"|a|={values['acceleration_norm_mg']:.1f} mg", flush=True)
            else:
                print(f"[{count}] {elapsed:.2f}s  {parsed['status']}  "
                      f"missing={','.join(parsed['missing_fields']) or 'none'}", flush=True)
            # A slow query never triggers a catch-up burst.
            finished = time.monotonic()
            if result["completion"] == "timeout":
                # Identical commands have no request ID. Retrying a timed-out
                # query could misattribute its delayed reply to the next sample.
                reason = "response_timeout"
                print("Stopping after timeout; delayed replies cannot be matched to a new query.",
                      file=sys.stderr)
                break
            next_query = now + interval
            if next_query <= finished:
                next_query = finished + interval
            if not result["answered"]:
                _drain(link, capture, min(deadline, finished + 0.2))
        capture.event("end", sample_count=count, valid_count=valid, failed_count=count - valid,
                      reason=reason, trigger_count=trigger_count, alert_states=tracker.states)
    print(f"Samples: {valid}/{count} valid -> {output}")
    if not count or valid != count:
        return 1
    return 3 if fail_on_alert and trigger_count else 0


def run_alerts(path: Path, limits, as_json: bool, fail_on_alert: bool) -> int:
    analysis = evaluate_capture(load_capture(path), limits)
    if as_json:
        print(json.dumps(analysis, ensure_ascii=True, allow_nan=False))
    else:
        print(f"Limit transitions: {analysis['trigger_count']} triggered; "
              f"{analysis['recovery_count']} recovered; "
              f"{analysis['unavailable_count']} unavailable; "
              f"{analysis['resumed_count']} resumed")
        print(f"Evaluated samples: {analysis['evaluated_count']}/{analysis['sample_count']}; "
              f"capture {analysis['capture_quality']['completion']}")
        for event in analysis["events"]:
            print(_terminal_text(f"[{event['index']}] {event['metric']}: "
                                 f"{event['transition']} ({event['state']}, value={event['value']})"))
        for metric, state in analysis["final_states"].items():
            print(f"Final {metric}: {state or 'unobserved'}")
    if fail_on_alert:
        if (not analysis["sample_count"] or analysis["failed_count"] or
                analysis["capture_quality"]["completion"] != "complete"):
            return 1
        if analysis["trigger_count"]:
            return 3
    return 0


def print_summary(path: Path, as_json: bool) -> int:
    data = load_capture(path)
    summary = data["summary"]
    if as_json:
        print(json.dumps(summary, ensure_ascii=True, allow_nan=False))
    else:
        print(f"Samples: {summary['valid_count']}/{summary['sample_count']} valid; "
              f"{summary['failed_count']} failed")
        for key, metric in METRICS.items():
            stats = summary["metrics"][key]
            if stats["count"]:
                deviation = "unavailable" if stats["stdev"] is None else f"{stats['stdev']:g}"
                print(f"{metric['label']}: min={stats['min']:g} "
                      f"mean={stats['mean']:g} max={stats['max']:g} {metric['unit']}; stdev={deviation}")
        timing = summary["timing"]
        print(f"Host timing basis: {timing['timestamp_basis']}")
        if timing["effective_hz"] is not None:
            print(f"Observed host record rate: {timing['effective_hz']:.3f} Hz")
        if timing["response_ms"]["mean"] is not None:
            print(f"Mean query processing time: {timing['response_ms']['mean']:.3f} ms")
        print(f"Capture completion: {summary['quality']['completion']}")
        for note in summary["quality"]["notes"]:
            print(f"Note: {note}")
        if not summary["sample_count"]:
            print("No sensor samples recorded.")
    return 0


def run_compare(baseline: Path, candidate: Path, as_json: bool, output: Path | None) -> int:
    from .comparison import compare_captures
    data = compare_captures(baseline, candidate)
    if output:
        from .comparison_report import write_comparison_report
        write_comparison_report(baseline, candidate, output, comparison=data)
    if as_json:
        compact = {key: {"source": data[key]["source"], "session": data[key]["session"],
                         "summary": data[key]["summary"]} for key in ("baseline", "candidate")}
        compact.update(board_match=data["board_match"], metrics=data["metrics"], notes=data["notes"])
        if output:
            compact["report"] = str(output.resolve())
        print(json.dumps(compact, ensure_ascii=True, allow_nan=False))
    else:
        print(f"Baseline: {_terminal_text(str(baseline))}\nCandidate: {_terminal_text(str(candidate))}")
        print(f"Board identity: {data['board_match']}")
        for key, metric in METRICS.items():
            stats = data["metrics"][key]
            difference = stats["mean_delta"]
            if difference is None:
                print(f"{metric['label']}: comparison unavailable")
            else:
                print(f"{metric['label']}: {stats['baseline_mean']:g} -> "
                      f"{stats['candidate_mean']:g}; delta={difference:+g} {metric['unit']}")
        for note in data["notes"]:
            print(f"Note: {note}")
        print("Differences describe the recordings; they do not establish a cause.")
        if output:
            print(f"Comparison report -> {output}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subs = result.add_subparsers(dest="command", required=True)
    devices = subs.add_parser("devices", help="list USB descriptors without opening ports")
    devices.add_argument("--json", action="store_true")
    serve = subs.add_parser("serve", help="open the local FlatSat management web app")
    serve.add_argument("--port", type=web_port, default=8765,
                       help="loopback HTTP port; 0 chooses an available port")
    serve.add_argument("--data-dir", type=Path,
                       help="capture storage directory; defaults to XDG_DATA_HOME/cuberange/flatsat/web")
    for name in ("monitor", "info", "watch"):
        cmd = subs.add_parser(name, help={"monitor": "receive USB bytes",
                              "info": "query local shell info", "watch": "poll local sensors"}[name])
        cmd.add_argument("--port", default="all" if name == "monitor" else "shell")
        cmd.add_argument("--serial", help="select a physical board by USB serial number")
        cmd.add_argument("--output", type=Path, help="new JSONL capture file; existing files are not overwritten")
        if name in ("monitor", "watch"):
            cmd.add_argument("--duration", type=positive_seconds, default=10.0)
        if name in ("info", "watch"):
            cmd.add_argument("--timeout", type=positive_seconds, default=2.0)
        if name == "watch":
            cmd.add_argument("--interval", type=positive_seconds, default=1.0,
                             help="seconds between query starts; overruns skip catch-up")
            cmd.add_argument("--label", help="experiment name recorded in the session")
            cmd.add_argument("--note", action="append", help="experiment conditions (repeatable)")
            cmd.add_argument("--limit", type=_limit_argument, action="append",
                             help="metric:min:max inclusive range; blank bound is unbounded (repeatable)")
            cmd.add_argument("--fail-on-alert", action="store_true",
                             help="exit 3 if any limit is exceeded; capture errors still exit 1")
        if name == "info":
            cmd.add_argument("--query", choices=QUERY_COMMANDS, action="append",
                             help="query just this command (repeatable); default fw_version/status/sensors")
    ping = subs.add_parser("ping", help="preview a FlatSat PING packet without sending it")
    ping.add_argument("--dry-run", action="store_true", help="required; this initial version does not transmit PING")
    ping.add_argument("--seq", type=lambda x: int(x, 0), default=1)
    ping.add_argument("--timestamp", type=lambda x: int(x, 0), default=0)
    decode = subs.add_parser("decode", help="inspect one Electronic Cats CCSDS packet offline")
    decode.add_argument("hex", help="complete packet in hex, including CRC")
    summary = subs.add_parser("summary", help="aggregate a sensor capture offline")
    summary.add_argument("capture", type=Path)
    summary.add_argument("--json", action="store_true")
    alerts = subs.add_parser("alerts", help="replay saved samples against limits offline")
    alerts.add_argument("capture", type=Path)
    alerts.add_argument("--limit", type=_limit_argument, action="append",
                        help="override all recorded limits with metric:min:max (repeatable)")
    alerts.add_argument("--json", action="store_true")
    alerts.add_argument("--fail-on-alert", action="store_true")
    report = subs.add_parser("report", help="create a standalone HTML sensor graph offline")
    report.add_argument("capture", type=Path)
    report.add_argument("--output", type=Path, help="new HTML file; existing files are not overwritten")
    report.add_argument("--limit", type=_limit_argument, action="append",
                        help="override recorded limits for the offline report (repeatable)")
    compare = subs.add_parser("compare", help="compare two saved experiments offline")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument("--json", action="store_true")
    compare.add_argument("--output", type=Path, help="create a new HTML comparison report")
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "serve":
            from .web import serve
            return serve(port=args.port, data_dir=args.data_dir)
        if args.command == "ping":
            if not args.dry_run:
                raise FlatSatError(
                    "Live PING is not supported by this initial USB tool. Use --dry-run. "
                    "The connected three-CDC firmware needs a verified local injection path.")
            print(json.dumps({"mode": "dry-run", "transmitted": False,
                              **decode_packet(build_ping(args.seq, args.timestamp))}))
            return 0
        if args.command == "decode":
            packet = decode_packet(bytes.fromhex(args.hex))
            print(json.dumps(packet))
            return 0 if packet["crc_valid"] else 1
        if args.command == "summary":
            return print_summary(args.capture, args.json)
        if args.command == "alerts":
            return run_alerts(args.capture, args.limit, args.json, args.fail_on_alert)
        if args.command == "compare":
            return run_compare(args.baseline, args.candidate, args.json, args.output)
        if args.command == "report":
            from .report import write_report
            output = args.output or _default_capture("report").with_suffix(".html")
            write_report(args.capture, output, limits=args.limit)
            print(f"Sensor report -> {output}")
            return 0
        if args.command == "watch":
            validate_limits(args.limit or [])
            if args.fail_on_alert and not args.limit:
                raise FlatSatError("--fail-on-alert requires at least one --limit")
        ports = discover_ports()
        if args.command == "devices":
            if args.json:
                print(json.dumps([asdict(p) for p in ports]))
            elif ports:
                for port in ports:
                    access = "ready" if port.accessible else "permission required"
                    print(f"{port.role:7} {port.path:16} {port.board_id!r}  {access}")
            else:
                print("No Electronic Cats FlatSat (1209:babc) found", file=sys.stderr)
            return 0 if ports else 1
        selected = select_ports(ports, args.port, args.serial)
        output = args.output or _default_capture(args.command)
        if args.command == "monitor":
            return run_monitor(selected, args.duration, output)
        if args.command == "watch":
            return run_watch(selected[0], args.duration, args.interval, args.timeout, output,
                             label=args.label, notes=args.note,
                             limits=args.limit, fail_on_alert=args.fail_on_alert)
        return run_info(selected[0], args.query or QUERY_COMMANDS[:3], args.timeout, output)
    except (FlatSatError, OSError, ValueError) as exc:
        print(f"FlatSat: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCapture stopped; received bytes remain in the JSONL file.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
