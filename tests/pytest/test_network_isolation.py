"""The containment check, driven with input designed to defeat it.

SECURITY.md lists a non-loopback bind as a reportable vulnerability, and until now the range did
exactly that by default: Renode 1.16.1 binds the Monitor and every socket terminal to 0.0.0.0, and
neither `--port` nor `CreateServerSocketTerminal` takes an address. The policy classified its own
behaviour. `cuberange.safety` is the answer - a network namespace holding only loopback - and this
file is the part that makes it evidence rather than an intention.

A containment check that cannot refuse is worse than none, because it reads as protection.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from cuberange.safety import isolate  # noqa: E402
from cuberange.safety.network import (OVERRIDE_ENV, NetworkIsolationError,  # noqa: E402
                                      assert_isolated_network, is_isolated,
                                      non_loopback_interfaces)


# --------------------------------------------------------------------------- the check itself

def test_a_loopback_only_namespace_is_accepted():
    assert_isolated_network(["lo"])
    assert is_isolated(["lo"])


@pytest.mark.parametrize("names", [
    ["lo", "eth0"],                      # the ordinary laptop
    ["lo", "docker0"],                   # a bridge that reaches other containers
    ["lo", "tailscale0"],                # a VPN, which is worse than eth0, not better
    ["lo", "eth0", "wlan0"],
])
def test_any_other_interface_is_refused(names):
    with pytest.raises(NetworkIsolationError) as exc:
        assert_isolated_network(names)
    for extra in non_loopback_interfaces(names):
        assert extra in str(exc.value), (
            f"the refusal does not name {extra}, so the reader cannot tell what is reachable")


def test_a_namespace_with_no_loopback_at_all_is_refused():
    """Isolation without usable loopback is a broken range, not a contained one.

    Measured: `unshare --user --net` without a uid map produces exactly this - only `lo`, and no
    CAP_NET_ADMIN to bring it up, so a loopback connect inside fails.
    """
    with pytest.raises(NetworkIsolationError) as exc:
        assert_isolated_network([])
    assert "loopback" in str(exc.value)


def test_the_check_does_not_trust_the_environment_variable():
    """isolate.py sets CUBERANGE_NETWORK_ISOLATED, and the check must not believe it.

    Any caller can export it. Interfaces are a fact about the namespace; the variable is a claim.
    """
    env = dict(os.environ, CUBERANGE_NETWORK_ISOLATED="1")
    env.pop(OVERRIDE_ENV, None)
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.run(
        [sys.executable, "-c",
         "from cuberange.safety.network import assert_isolated_network as a; a()"],
        capture_output=True, text=True, env=env, timeout=60)
    assert proc.returncode != 0, (
        "setting CUBERANGE_NETWORK_ISOLATED=1 was enough to pass the check on a host with real "
        "interfaces; the flag is advisory and must not be load-bearing")
    assert "refusing to start Renode" in proc.stderr


def test_the_override_is_explicit_and_named_in_the_refusal():
    """A check people cannot satisfy gets deleted. The way out must be visible and deliberate."""
    with pytest.raises(NetworkIsolationError) as exc:
        assert_isolated_network(["lo", "eth0"])
    assert OVERRIDE_ENV in str(exc.value)

    os.environ[OVERRIDE_ENV] = "1"
    try:
        assert_isolated_network(["lo", "eth0"])          # deliberate, and now permitted
    finally:
        del os.environ[OVERRIDE_ENV]

    os.environ[OVERRIDE_ENV] = "true"                    # anything but "1" is not the override
    try:
        with pytest.raises(NetworkIsolationError):
            assert_isolated_network(["lo", "eth0"])
    finally:
        del os.environ[OVERRIDE_ENV]


# --------------------------------------------------------------------------- the launcher

def test_at_least_one_backend_works_on_this_host():
    """If none did, every e2e target would refuse and the range would be unrunnable here."""
    results = {name: isolate.probe(name, build) for name, build in isolate.BACKENDS}
    working = [n for n, (ok, _) in results.items() if ok]
    assert working, (
        "no isolation backend works here, so `make check` cannot run:\n  "
        + "\n  ".join(f"{n}: {d}" for n, (_, d) in results.items()))


def test_the_backend_probe_actually_runs_the_backend():
    """`shutil.which` is not a probe.

    On this host `unshare` is installed and refused by AppArmor
    (kernel.apparmor_restrict_unprivileged_userns=1), so presence and usability differ. A
    which-based check would have chosen it and every run would have failed inside the namespace.
    """
    ok, detail = isolate.probe("definitely-not-a-real-binary", lambda cmd: list(cmd))
    assert not ok and "not installed" in detail

    # A backend that runs but does NOT isolate must be rejected on what it produced.
    ok, detail = isolate.probe(sys.executable.split("/")[-1], lambda cmd: list(cmd))
    assert not ok, "a command that never entered a namespace was accepted as a working backend"
    assert "interfaces" in detail, detail


def test_a_command_run_through_the_launcher_sees_only_loopback():
    """End to end, in a child process, because this test itself is not isolated."""
    if not any(isolate.probe(n, b)[0] for n, b in isolate.BACKENDS):
        pytest.fail("no working backend; test_at_least_one_backend_works_on_this_host explains")
    env = dict(os.environ, PYTHONPATH=str(REPO / "src"))
    proc = subprocess.run(
        [sys.executable, "-m", "cuberange.safety.isolate", "--",
         sys.executable, "-c",
         "import socket;print(sorted(n for _,n in socket.if_nameindex()))"],
        capture_output=True, text=True, env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "['lo']", proc.stdout


def test_the_supervisor_consults_the_check_by_default(monkeypatch):
    """The default is the point: containment is opt-out, not opt-in.

    Written against the call rather than against this host's interfaces. The first version
    compared against the real namespace and skipped when the process was already isolated - which
    is precisely what happens if the unit tests are ever run under the isolator, and a check that
    quietly stops running is the failure this repository keeps finding in its own work.
    """
    from cuberange.renode import supervisor as sup_mod

    calls = []

    def spy():
        calls.append(True)
        raise NetworkIsolationError("spy")

    monkeypatch.setattr(sup_mod, "assert_isolated_network", spy)

    with pytest.raises(NetworkIsolationError):
        sup_mod.RenodeSupervisor(cwd=REPO)
    assert calls == [True], "the default constructor did not consult the containment check"

    sup_mod.RenodeSupervisor(cwd=REPO, require_isolation=False)   # the documented opt-out
    assert calls == [True], "require_isolation=False still consulted the check"
