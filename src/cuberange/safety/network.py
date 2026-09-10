"""Refuse to start Renode anywhere its sockets can be reached from outside.

THE PROBLEM, MEASURED. Renode 1.16.1 binds its Monitor and every socket terminal to 0.0.0.0.
`--port` takes a port and no address, and `CreateServerSocketTerminal(port, name, telnetMode,
flushOnConnect)` has no bind parameter, so there is nothing to configure. `/proc/net/tcp` during a
run shows `00000000:0EC1`, not `0100007F:0EC1`.

WHY THAT MATTERS HERE AND NOT IN A NORMAL EMULATOR RUN. The listeners are the exercise. The CAN
injector accepts raw frames from anyone who connects; the space link accepts telecommands with no
authentication, because a link that authenticated would have no EX-L01. SAFE_USE.md said the range
binds loopback, which was wrong, and SECURITY.md listed a non-loopback bind as a reportable
vulnerability - so the policy classified the range's own default behaviour.

THE FIX. Run the whole exercise in a network namespace that contains only loopback. Then 0.0.0.0
is a promise about interfaces that do not exist. This module is the check; `isolate.py` is the
launcher. The check fails closed: no namespace, no Renode.

Checking interface names beats comparing namespace inode numbers, because a container can have a
namespace of its own and still have a routed eth0. A down interface is rejected too - it can be
brought up while an exercise is running.
"""
from __future__ import annotations

import os
import socket
from typing import Iterable

#: Set by isolate.py inside the namespace. Advisory only - the interface check below is what
#: decides, because an environment variable is something any caller can claim.
ISOLATED_ENV = "CUBERANGE_NETWORK_ISOLATED"

#: The deliberate way out, for a host where no namespace mechanism works at all. It must be set on
#: purpose and it is named in the error message, because a containment check people cannot satisfy
#: gets deleted rather than obeyed - and a deleted check protects nobody.
OVERRIDE_ENV = "CUBERANGE_ALLOW_UNISOLATED"


class NetworkIsolationError(RuntimeError):
    """Raised before Renode starts outside a loopback-only namespace."""


def non_loopback_interfaces(names: Iterable[str]) -> tuple[str, ...]:
    """Every interface that is not Linux loopback, in a stable order."""
    return tuple(sorted(n for n in names if n != "lo"))


def interface_names() -> list[str]:
    try:
        return [name for _index, name in socket.if_nameindex()]
    except OSError as exc:                                    # pragma: no cover - needs a broken host
        raise NetworkIsolationError(
            "cannot inspect network interfaces in the current namespace") from exc


def is_isolated(names: Iterable[str] | None = None) -> bool:
    names = list(interface_names() if names is None else names)
    return not non_loopback_interfaces(names) and "lo" in names


def assert_isolated_network(names: Iterable[str] | None = None) -> None:
    """Require a namespace whose only interface is `lo`, or an explicit, deliberate override."""
    names = list(interface_names() if names is None else names)
    external = non_loopback_interfaces(names)

    if not external and "lo" in names:
        return

    if os.environ.get(OVERRIDE_ENV) == "1":
        return

    if not external and "lo" not in names:
        raise NetworkIsolationError(
            "refusing to start Renode: the namespace has no loopback interface, so the range "
            "could not talk to itself either. Bring `lo` up.")

    raise NetworkIsolationError(
        "refusing to start Renode outside an isolated network namespace. Renode 1.16.1 binds its "
        "Monitor and every socket terminal to 0.0.0.0 with no way to configure an address, and "
        "this range's listeners accept unauthenticated telecommands and raw CAN frames by design. "
        f"Reachable interfaces here: {', '.join(external)}.\n"
        "  Run it isolated:  python3 -m cuberange.safety.isolate -- <your command>\n"
        f"  Or, deliberately, on a host where no namespace mechanism works: {OVERRIDE_ENV}=1")
