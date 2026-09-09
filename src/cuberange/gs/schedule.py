"""Time-based telecommand scheduling, and the path by which an outsider gets into it.

A ground segment does not send most of its commands by hand. An operator builds a plan, the plan
sits in a store, and something transmits each entry when its time comes. Plans also arrive from
elsewhere: a pass predictor, a partner agency, a payload team's own tooling. That import path is
the subject of EX-G01.

WHAT MAKES THIS AN EXERCISE RATHER THAN A TAUTOLOGY. The design's second revision proposed an
EX-G01 that amounted to "whoever can write the database can write the database", and section 16
records that being thrown out. So the intrusion mechanism is named and it is a real one:

    plugins/                 third-party tools drop their OUTPUT here - pass predictions,
                             conjunction reports, whatever a mission integrates
    <the importer reads it>  and the vulnerable ground segment ingests schedule files from the
                             same directory

One directory, two trust levels. A plugin that is only ever supposed to write a report can
therefore write a plan, and the scheduler transmits it with the operator's own link and the
operator's own credentials. The attacker never touches the spacecraft and never needs to: they
author, compromise, or simply persuade someone to install a plugin.

The scheduler here is ONE implementation. What differs between the vulnerable and the mitigated
ground segment is the policy object it is given, and nothing else - which is the host-side analogue
of the firmware pairs' single build flag. `tests/pytest/test_schedule_policy.py` asserts that
analogue rather than asking a reader to take it on trust.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from .import_policy import ImportPolicy, ImportRejected


@dataclass(frozen=True)
class ScheduledCommand:
    """One planned telecommand.

    `due` is a monotonic-ish wall clock, because the ground segment is a host program and does not
    live in the emulation's virtual time. That is a real difference from everything on the
    spacecraft side and it is worth stating: a schedule entry's time has no relationship to the
    virtual seconds a Renode scenario counts.
    """

    due: float
    service: int
    subtype: int
    args: bytes
    origin: str          # "operator" or the name of the plugin file it was imported from
    note: str = ""

    def as_row(self) -> tuple:
        return (self.due, self.service, self.subtype, self.args.hex(), self.origin, self.note)


class ScheduleStore:
    """The plan. SQLite because a real one is a database and the file is the thing an attacker
    is trying to influence; nothing here depends on it being SQLite."""

    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        self._db = sqlite3.connect(self.path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS schedule ("
            " due REAL, service INTEGER, subtype INTEGER, args TEXT, origin TEXT, note TEXT)")
        self._db.commit()

    def add(self, cmd: ScheduledCommand) -> None:
        self._db.execute("INSERT INTO schedule VALUES (?,?,?,?,?,?)", cmd.as_row())
        self._db.commit()

    def due_now(self, now: Optional[float] = None) -> list[ScheduledCommand]:
        now = time.time() if now is None else now
        rows = self._db.execute(
            "SELECT due, service, subtype, args, origin, note FROM schedule WHERE due <= ?"
            " ORDER BY due", (now,)).fetchall()
        return [ScheduledCommand(due=r[0], service=r[1], subtype=r[2],
                                 args=bytes.fromhex(r[3]), origin=r[4], note=r[5]) for r in rows]

    def remove(self, cmd: ScheduledCommand) -> None:
        self._db.execute(
            "DELETE FROM schedule WHERE due=? AND service=? AND subtype=? AND args=? AND origin=?",
            (cmd.due, cmd.service, cmd.subtype, cmd.args.hex(), cmd.origin))
        self._db.commit()

    def all(self) -> list[ScheduledCommand]:
        rows = self._db.execute(
            "SELECT due, service, subtype, args, origin, note FROM schedule ORDER BY due").fetchall()
        return [ScheduledCommand(due=r[0], service=r[1], subtype=r[2],
                                 args=bytes.fromhex(r[3]), origin=r[4], note=r[5]) for r in rows]

    def close(self) -> None:
        self._db.close()


class Scheduler:
    """Imports plans and transmits what is due.

    One implementation, two policies. `policy` decides what an imported file is allowed to be; it
    does not decide how anything is transmitted, so a mitigation cannot accidentally become "the
    hardened build sends differently".
    """

    def __init__(self, store: ScheduleStore, policy: ImportPolicy,
                 import_dir: Path | str, send: Callable[[ScheduledCommand], None]):
        self.store = store
        self.policy = policy
        self.import_dir = Path(import_dir)
        self._send = send
        self.rejected: list[tuple[str, str]] = []   # (filename, why) - evidence, not noise
        self.sent: list[ScheduledCommand] = []

    def import_pending(self) -> int:
        """Read every *.plan.json in the import directory. Returns how many entries were accepted.

        Files are not deleted. A ground segment that consumed its inputs would destroy the evidence
        an incident responder needs, and on this range that evidence is half the lesson.
        """
        accepted = 0
        if not self.import_dir.is_dir():
            return 0
        for path in sorted(self.import_dir.glob("*.plan.json")):
            try:
                raw = path.read_bytes()
                entries = self.policy.accept(raw, source=path.name)
            except ImportRejected as exc:
                self.rejected.append((path.name, str(exc)))
                continue
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                self.rejected.append((path.name, f"unreadable: {exc}"))
                continue
            for entry in entries:
                self.store.add(entry)
                accepted += 1
        return accepted

    def tick(self, now: Optional[float] = None) -> list[ScheduledCommand]:
        """Transmit everything whose time has come. Returns what was sent."""
        fired = []
        for cmd in self.store.due_now(now):
            self._send(cmd)
            self.store.remove(cmd)
            self.sent.append(cmd)
            fired.append(cmd)
        return fired


def operator_entry(due: float, service: int, subtype: int, args: bytes,
                   note: str = "") -> ScheduledCommand:
    """A command the operator put in the plan directly, without going through an import."""
    return ScheduledCommand(due=due, service=service, subtype=subtype, args=args,
                            origin="operator", note=note)


def plan_document(entries: Iterable[dict]) -> bytes:
    """Serialise a plan the way the importer expects to read one."""
    return json.dumps({"version": 1, "entries": list(entries)}, sort_keys=True).encode()
