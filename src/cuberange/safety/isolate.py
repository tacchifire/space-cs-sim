"""Run a command in a network namespace that contains only loopback.

    python3 -m cuberange.safety.isolate -- make demo-p0
    python3 -m cuberange.safety.isolate --probe

WHY TWO BACKENDS, AND WHY THEY ARE PROBED RATHER THAN ASSUMED.

`unshare --user --map-root-user --net` is the obvious mechanism and it does not work on this
machine, or on any current Ubuntu with the default AppArmor policy:

    kernel.apparmor_restrict_unprivileged_userns = 1
    unshare: write failed /proc/self/uid_map: Operation not permitted

The namespace is created; only the uid map is refused. That matters, because without the map the
process has no CAP_NET_ADMIN inside and cannot bring `lo` up - measured: the namespace really does
contain only `lo`, and a loopback connect inside it fails. Isolation without usable loopback is
not isolation, it is a broken range.

bubblewrap does work here, measured the same way: interfaces `['lo']` and a loopback connect that
succeeds. So the backend is chosen by running each candidate and looking at what it produced, not
by checking which binary exists. A tool that is installed and refused by policy is exactly the case
a `shutil.which` check gets wrong, and it is the case this host is in.

Only the network is unshared. The filesystem is bound through as-is, because the range needs its
build directory, its logs and the Zephyr SDK, and because a sandbox that also hid those would fail
in ways that look like range defects.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import Sequence

from .network import ISOLATED_ENV, NetworkIsolationError

#: A candidate is (name, argv-prefix builder). Each is tried by running the probe below inside it.
_PROBE = (
    "import socket,sys\n"
    "names=sorted(n for _,n in socket.if_nameindex())\n"
    "if names!=['lo']: sys.exit('interfaces %r' % names)\n"
    "s=socket.socket(); s.bind(('127.0.0.1',0)); s.listen(1)\n"
    "c=socket.create_connection(('127.0.0.1',s.getsockname()[1]),timeout=3)\n"
    "c.close(); s.close(); print('ok')\n"
)


def _bwrap(cmd: Sequence[str]) -> list[str]:
    return ["bwrap", "--dev-bind", "/", "/", "--unshare-net", *cmd]


def _unshare(cmd: Sequence[str]) -> list[str]:
    # `sh -c` because loopback must be brought up inside the namespace before the command runs,
    # and unshare has no hook for that.
    ip = shutil.which("ip") or "/usr/sbin/ip"
    inner = f'{ip} link set lo up && exec "$@"'
    return ["unshare", "--user", "--map-root-user", "--net", "sh", "-c", inner, "--", *cmd]

BACKENDS = (("bwrap", _bwrap), ("unshare", _unshare))


def probe(name: str, build) -> tuple[bool, str]:
    """Actually run the probe under this backend and report what happened."""
    if shutil.which(name) is None:
        return False, "not installed"
    try:
        proc = subprocess.run(build([sys.executable, "-c", _PROBE]),
                              capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"could not run: {exc}"
    if proc.returncode == 0 and proc.stdout.strip() == "ok":
        return True, "loopback-only namespace with working loopback"
    detail = (proc.stderr.strip() or proc.stdout.strip() or f"exit {proc.returncode}")
    return False, detail.splitlines()[-1][:160]


def working_backend():
    """The first backend that demonstrably produces a usable isolated namespace."""
    failures = []
    for name, build in BACKENDS:
        ok, detail = probe(name, build)
        if ok:
            return name, build
        failures.append(f"  {name}: {detail}")
    raise NetworkIsolationError(
        "no working network-namespace mechanism on this host, so the range cannot be contained:\n"
        + "\n".join(failures)
        + "\n\nInstall bubblewrap (`bwrap`), which needs no root and no kernel policy change, or "
          "run the range on a machine that is not sharing a network you care about and set "
          "CUBERANGE_ALLOW_UNISOLATED=1 to say so deliberately.")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--probe", action="store_true",
                    help="report which backends work here and exit")
    ap.add_argument("command", nargs="*", help="the command to run, after --")
    args = ap.parse_args(list(argv) if argv is not None else None)

    if args.probe:
        for name, build in BACKENDS:
            ok, detail = probe(name, build)
            print(f"  {'OK  ' if ok else 'no  '} {name}: {detail}")
        return 0

    if not args.command:
        ap.error("nothing to run; pass the command after --")

    name, build = working_backend()
    env = dict(os.environ, **{ISOLATED_ENV: "1", "CUBERANGE_ISOLATION_BACKEND": name})
    os.execvpe("env", ["env", *(f"{k}={v}" for k, v in env.items()),
                       *build(list(args.command))], env)
    return 127                                                # pragma: no cover - execvpe replaces us


if __name__ == "__main__":
    sys.exit(main())
