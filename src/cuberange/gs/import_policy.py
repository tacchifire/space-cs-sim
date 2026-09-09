"""What an imported plan is allowed to be. The whole of EX-G01's vulnerable/mitigated difference.

Two policies, one scheduler. `Scheduler` never branches on which policy it holds, so a mitigation
cannot leak into how commands are transmitted - the host-side analogue of the firmware pairs
differing by one build flag, and `tests/pytest/test_schedule_policy.py` asserts it rather than
asserting that somebody was careful.

    AcceptAnything          parses the file and trusts it. No schema, no provenance, no bound on
                            what an imported entry may command.
    SignedAndBounded        requires an HMAC the operator's key produces, validates the schema,
                            and refuses services an import has no business scheduling.

The key is a fixed string committed in the clear, like the EPS power token, and for the same
reason: it is the cheapest control that makes the difference between "any file in that directory is
a plan" and "not any file", which is the lesson. mitigation.md says what it does not solve.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:                       # pragma: no cover - import cycle at runtime only
    from .schedule import ScheduledCommand

# Committed in the clear on purpose. See SECURITY.md's table of intended weaknesses.
IMPORT_KEY = b"cuberange-operator-plan-key"

# Services an imported plan may schedule. 17 is the connection test and 3 is housekeeping: things a
# partner's pass plan legitimately asks for. 8 - function management, which is how the ground
# switches a power rail - is not on the list, and that is the point.
IMPORT_ALLOWED_SERVICES = frozenset({3, 17})


class ImportRejected(Exception):
    """The file was not an acceptable plan. The message is what a responder reads."""


class ImportPolicy(Protocol):
    def accept(self, raw: bytes, source: str) -> list["ScheduledCommand"]:
        ...


def _entries_from(doc: dict, source: str) -> list["ScheduledCommand"]:
    from .schedule import ScheduledCommand

    out = []
    for e in doc.get("entries", []):
        out.append(ScheduledCommand(
            due=float(e["due"]), service=int(e["service"]), subtype=int(e["subtype"]),
            args=bytes.fromhex(e.get("args", "")), origin=source, note=e.get("note", "")))
    return out


class AcceptAnything:
    """The vulnerable policy: if it parses, it is a plan.

    Note what is NOT here rather than what is. There is no check that the file came from the
    operator, no check that its fields are the ones a plan has, and no check that the command it
    schedules is one an import may ask for. Each of those is a separate control, and this exercise
    is about a system that had none of them while looking like it had a plan-import feature.
    """

    name = "accept-anything"

    def accept(self, raw: bytes, source: str) -> list["ScheduledCommand"]:
        doc = json.loads(raw.decode())
        return _entries_from(doc, source)


class SignedAndBounded:
    """The mitigated policy: provenance, then shape, then authority.

    In that order deliberately. Checking the schema of a document you have no reason to trust is
    work done on an attacker's behalf, and rejecting on a schema error tells them which field to
    fix next.
    """

    name = "signed-and-bounded"

    def __init__(self, key: bytes = IMPORT_KEY,
                 allowed_services: frozenset = IMPORT_ALLOWED_SERVICES):
        self._key = key
        self._allowed = allowed_services

    def accept(self, raw: bytes, source: str) -> list["ScheduledCommand"]:
        try:
            doc = json.loads(raw.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImportRejected(f"not JSON: {exc}") from exc
        if not isinstance(doc, dict):
            raise ImportRejected("a plan is an object")

        # 1. Provenance.
        sig = doc.pop("sig", None)
        if not isinstance(sig, str):
            raise ImportRejected("unsigned plan")
        if not hmac.compare_digest(sig, sign(doc, self._key)):
            raise ImportRejected("signature does not match the operator key")

        # 2. Shape. Only now, when the document is known to be one the operator produced.
        if doc.get("version") != 1:
            raise ImportRejected(f"unsupported plan version {doc.get('version')!r}")
        entries = doc.get("entries")
        if not isinstance(entries, list):
            raise ImportRejected("entries must be a list")
        for e in entries:
            if not isinstance(e, dict):
                raise ImportRejected("each entry must be an object")
            missing = {"due", "service", "subtype"} - set(e)
            if missing:
                raise ImportRejected(f"entry is missing {sorted(missing)}")

        # 3. Authority. A valid signature means the operator produced the file; it does not mean
        #    every command in it is one an IMPORT may schedule. Those are different questions and
        #    conflating them is how a signed-import feature becomes a signed-anything feature.
        for e in entries:
            service = int(e["service"])
            if service not in self._allowed:
                raise ImportRejected(
                    f"service {service} may not be scheduled by import "
                    f"(allowed: {sorted(self._allowed)})")

        return _entries_from(doc, source)


def sign(doc: dict, key: bytes = IMPORT_KEY) -> str:
    """The operator's signature over a plan, computed the way SignedAndBounded checks it.

    Over the canonical JSON of the document WITHOUT its own signature field, so the same document
    signs and verifies identically regardless of key order in the file.
    """
    body = {k: v for k, v in doc.items() if k != "sig"}
    payload = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def signed_plan(doc: dict, key: bytes = IMPORT_KEY) -> bytes:
    """A plan document with a valid operator signature attached."""
    out = {k: v for k, v in doc.items() if k != "sig"}
    out["sig"] = sign(out, key)
    return json.dumps(out, sort_keys=True).encode()
