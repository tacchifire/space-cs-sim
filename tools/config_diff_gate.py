#!/usr/bin/env python3
"""The anti-strawman gate: prove each vulnerable/mitigated firmware pair differs by one flag.

CubeRange's central claim, and the thing that separates it from a scripted CTF, is that its
vulnerabilities are not manufactured by turning defences off. CONTRIBUTING.md states the proof as a
`diff` a contributor is asked to run by hand, and Makefile:109 described it as "a CI gate" that did
not exist -- the same shape as W23, W24 and W26 in the design's section 16, where documentation
described a verification mechanism that was never built.

This is that gate. It reads firmware-matrix.yml and, for every declared pair, asserts:

  1. KCONFIG IDENTICAL   the two builds' CONFIG_* sets match exactly.
  2. ONE FLAG            no CUBERANGE_* cache variable other than the declared one differs.
  3. ELFS DIFFER         the two zephyr.elf files are not byte-identical.
  4. FLAG IS LIVE        the guarded symbol is actually referenced by the application source.
  5. SAME COMPILER       every application translation unit is compiled with an identical command
                         line apart from the declared -D.

Checks 3 and 4 exist because 1 and 2 can both pass while the flag does nothing whatsoever -- a
misspelled cache variable produces two identical images and a perfectly clean diff, and the pair
would then "prove" a mitigation that was never compiled in. That is a vacuous pass, and this
project has shipped one before: an earlier probe.sh reported PASS for all 34 checks when its output
directory was missing, because grep on a nonexistent file finds no error.

Check 5 closes the hole the first four leave open. Kconfig and the CMake cache say nothing about
compiler options, so a CMakeLists that added `-fno-stack-protector` or dropped an optimisation
level on the vulnerable side would pass 1-4 while manufacturing the vulnerability exactly the way
CONTRIBUTING.md forbids. Measured on the shipped pairs: the app and common translation units differ
by precisely one argument, the declared -D.

For the same reason this gate carries --self-test, which feeds it known-bad inputs and requires
them to be REJECTED. A gate that cannot fail is not evidence.

Usage:
    python3 tools/config_diff_gate.py [--out DIR] [--matrix FILE]
    python3 tools/config_diff_gate.py --self-test

    python3 tools/config_diff_gate.py            # this checkout's build directory

Exit code 0 only if every declared pair passed every check, or -- under --self-test -- if every
deliberately broken case was detected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from cuberange.paths import out_dir  # noqa: E402

GREEN, RED, YELLOW, BOLD, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[1m", "\033[0m"


class GateError(Exception):
    """A pair failed a check. The message is the evidence."""


# --------------------------------------------------------------------------- readers

def read_kconfig(build: Path) -> dict[str, str]:
    """The CONFIG_* assignments Zephyr resolved for this build.

    Comment lines matter: Kconfig writes `# CONFIG_FOO is not set` for a disabled symbol, and
    treating that as absent rather than as an explicit "off" would let a pair that turns a
    protection off read as identical to one that never mentioned it.
    """
    path = build / "zephyr" / ".config"
    if not path.is_file():
        raise GateError(f"no Kconfig output at {path} - build the pair first")
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if m := re.fullmatch(r"(CONFIG_[A-Za-z0-9_]+)=(.*)", line):
            out[m.group(1)] = m.group(2)
        elif m := re.fullmatch(r"# (CONFIG_[A-Za-z0-9_]+) is not set", line):
            out[m.group(1)] = "<not set>"
    if not out:
        raise GateError(f"{path} contains no CONFIG_ symbols - refusing to call that a match")
    return out


def read_source_dir(build: Path) -> str:
    """Which source tree produced this build directory.

    CMakeCache.txt records CMAKE_HOME_DIRECTORY, and checking it is not paranoia: $OUT defaults to
    /tmp/cuberange for every checkout of this repository, so two working copies on one machine
    build into the SAME directory and silently overwrite each other's images. That happened here -
    a second checkout was building into /tmp/cuberange while this gate ran, and the failure it
    produced looked like "the pair differs by four cache variables" rather than "these artifacts
    are somebody else's".

    Set OUT to something per-checkout, and let this check tell you when you have not.
    """
    path = build / "CMakeCache.txt"
    if not path.is_file():
        raise GateError(f"no CMakeCache.txt at {path} - build the pair first")
    for line in path.read_text(errors="replace").splitlines():
        if m := re.fullmatch(r"CMAKE_HOME_DIRECTORY:INTERNAL=(.*)", line.strip()):
            return m.group(1)
    raise GateError(f"{path} does not record CMAKE_HOME_DIRECTORY")


def read_cache_vars(build: Path, prefix: str = "CUBERANGE_") -> dict[str, str]:
    """The project's own CMake cache variables. Paths and compiler probes are ignored on purpose:
    they legitimately differ between two build directories."""
    path = build / "CMakeCache.txt"
    if not path.is_file():
        raise GateError(f"no CMakeCache.txt at {path} - build the pair first")
    out: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if m := re.fullmatch(rf"({prefix}[A-Za-z0-9_]+):[A-Z]+=(.*)", line.strip()):
            out[m.group(1)] = m.group(2)
    return out


def read_compile_commands(build: Path) -> dict[str, list[str]]:
    """Compile command lines for CubeRange's own sources, keyed by file.

    Zephyr's own translation units are excluded: there are thousands, and they carry the build
    directory in include paths, so comparing them all would be slow and would report differences
    that are only ever the two directory names. The build directory is normalised out of what is
    compared for the same reason.
    """
    path = build / "compile_commands.json"
    if not path.is_file():
        raise GateError(f"no compile_commands.json at {path} - build the pair first")
    rows = json.loads(path.read_text())
    out: dict[str, list[str]] = {}
    for row in rows:
        src = row["file"]
        if "/firmware/apps/" not in src and "/firmware/common/" not in src:
            continue
        cmd = row.get("command") or " ".join(row.get("arguments", []))
        out[src] = cmd.replace(str(build), "<BUILD>").split()
    if not out:
        raise GateError(
            f"{path} lists no CubeRange sources; refusing to call an empty comparison a match")
    return out


def sha256(path: Path) -> str:
    if not path.is_file():
        raise GateError(f"missing artifact {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --------------------------------------------------------------------------- checks

def check_pair(pair: dict, out_dir: Path) -> list[str]:
    """Run every check. Returns the evidence lines; raises GateError on the first failure."""
    pid, flag = pair["id"], pair["flag"]
    vuln = out_dir / pair["vuln"]["build"]
    hard = out_dir / pair["hard"]["build"]
    evidence: list[str] = []

    # 0. Provenance. Both halves must have been built from THIS source tree, or nothing below is
    #    about this repository. Checked first because every later message is misleading otherwise.
    app = (REPO / pair["app"]).resolve()
    for label, build in (("vulnerable", vuln), ("mitigated", hard)):
        home = Path(read_source_dir(build)).resolve()
        if home != app:
            raise GateError(
                f"{pid}: the {label} build in {build.name} was produced from {home}, not from "
                f"{app}. $OUT is {out_dir} and it defaults to the same path for every checkout of "
                f"this repository, so two working copies overwrite each other's images. Set OUT "
                f"per-checkout and rebuild.")
    evidence.append("both halves built from this source tree")

    # 1. Kconfig identical.
    kv, kh = read_kconfig(vuln), read_kconfig(hard)
    if kv != kh:
        diff = sorted(
            f"    {k}: vuln={kv.get(k, '<absent>')!r} hard={kh.get(k, '<absent>')!r}"
            for k in set(kv) | set(kh) if kv.get(k) != kh.get(k)
        )
        raise GateError(
            f"{pid}: the two builds resolve different Kconfig symbols, so the vulnerability may be "
            f"manufactured by weakening the platform:\n" + "\n".join(diff))
    evidence.append(f"{len(kv)} Kconfig symbols identical")

    # 2. Exactly one CUBERANGE_* cache variable differs, and it is the declared one.
    cv, ch = read_cache_vars(vuln), read_cache_vars(hard)
    differing = {k for k in set(cv) | set(ch) if cv.get(k) != ch.get(k)}
    if differing != {flag}:
        raise GateError(
            f"{pid}: expected exactly {flag} to differ, but the differing cache variables are "
            f"{sorted(differing) or 'none'} "
            f"(vuln={ {k: cv.get(k) for k in differing} }, hard={ {k: ch.get(k) for k in differing} })")
    if cv[flag] != pair["vuln"]["value"] or ch[flag] != pair["hard"]["value"]:
        raise GateError(
            f"{pid}: {flag} is {cv[flag]!r}/{ch[flag]!r} but the matrix declares "
            f"{pair['vuln']['value']!r}/{pair['hard']['value']!r}")
    evidence.append(f"only {flag} differs ({cv[flag]} -> {ch[flag]})")

    # 3. The images actually differ. Without this, a misspelled flag passes checks 1 and 2 while
    #    compiling the identical binary twice, and the pair proves nothing.
    hv, hh = sha256(vuln / "zephyr" / "zephyr.elf"), sha256(hard / "zephyr" / "zephyr.elf")
    if hv == hh:
        raise GateError(
            f"{pid}: both builds produced the same ELF ({hv[:16]}...), so {flag} changed nothing. "
            f"Check that the C source reads {flag} under the same spelling CMakeLists.txt uses.")
    evidence.append(f"ELFs differ ({hv[:12]}... vs {hh[:12]}...)")

    # 4. The flag is referenced by the application source, not merely defined by the build system.
    app = REPO / pair["app"]
    sources = [p for p in app.rglob("*.c")] + [p for p in app.rglob("*.h")]
    if not any(flag in p.read_text(errors="replace") for p in sources):
        raise GateError(
            f"{pid}: {flag} is passed to the compiler but no source under {pair['app']} mentions it")
    evidence.append(f"{flag} referenced in {pair['app']}")

    # 5. Identical compiler invocation apart from the declared -D. This is what stops a
    #    vulnerability being manufactured through compiler options, which checks 1-4 cannot see.
    ccv, cch = read_compile_commands(vuln), read_compile_commands(hard)
    if set(ccv) != set(cch):
        only_v = sorted(Path(f).name for f in set(ccv) - set(cch))
        only_h = sorted(Path(f).name for f in set(cch) - set(ccv))
        raise GateError(
            f"{pid}: the two builds compile different source sets "
            f"(only in vulnerable: {only_v}, only in mitigated: {only_h})")
    allowed = {f"-D{flag}={pair['vuln']['value']}", f"-D{flag}={pair['hard']['value']}"}
    for src in sorted(ccv):
        a, b = ccv[src], cch[src]
        extra = (set(a) ^ set(b)) - allowed
        if extra:
            raise GateError(
                f"{pid}: {Path(src).name} is compiled differently beyond {flag}: "
                f"{sorted(extra)}. A vulnerability guarded by compiler options rather than by the "
                f"declared flag is manufactured by weakening the platform.")
        if len(a) != len(b):
            raise GateError(
                f"{pid}: {Path(src).name} compile lines differ in length ({len(a)} vs {len(b)}) "
                f"with no unexpected argument - an argument is repeated a different number of times")
    evidence.append(f"{len(ccv)} translation units compiled identically apart from -D{flag}")

    return evidence


def run(matrix_path: Path, out_dir: Path) -> int:
    matrix = yaml.safe_load(matrix_path.read_text())
    pairs = matrix.get("pairs") or []
    if not pairs:
        print(f"{RED}{matrix_path} declares no pairs - an empty matrix is not a passing gate{RESET}")
        return 1

    print(f"{BOLD}== firmware pair gate =={RESET}")
    print(f"   matrix: {matrix_path.relative_to(REPO) if matrix_path.is_relative_to(REPO) else matrix_path}")
    print(f"   builds: {out_dir}")
    failed = 0
    for pair in pairs:
        try:
            for line in check_pair(pair, out_dir):
                print(f"  {GREEN}PASS{RESET}  {pair['id']}: {line}")
        except GateError as exc:
            failed += 1
            print(f"  {RED}FAIL{RESET}  {exc}")
    print()
    if failed:
        print(f"{RED}{failed} of {len(pairs)} pairs failed{RESET}")
        return 1
    print(f"{GREEN}all {len(pairs)} pairs differ by exactly one declared flag{RESET}")
    return 0


# --------------------------------------------------------------------------- self-test

def _fake_build(root: Path, name: str, kconfig: dict[str, str], cache: dict[str, str],
                elf: bytes, cflags: str = "", home: str = "/somewhere/else") -> Path:
    build = root / name
    (build / "zephyr").mkdir(parents=True, exist_ok=True)
    (build / "zephyr" / ".config").write_text(
        "\n".join(f"{k}={v}" if v != "<not set>" else f"# {k} is not set"
                  for k, v in kconfig.items()) + "\n")
    (build / "CMakeCache.txt").write_text(
        f"CMAKE_HOME_DIRECTORY:INTERNAL={home}\n"
        + "\n".join(f"{k}:STRING={v}" for k, v in cache.items()) + "\n")
    (build / "zephyr" / "zephyr.elf").write_bytes(elf)
    flagval = cache.get("CUBERANGE_TEST_FLAG", "0")
    (build / "compile_commands.json").write_text(json.dumps([{
        "file": "/repo/firmware/apps/x/src/main.c",
        "command": f"cc -Os -DCUBERANGE_TEST_FLAG={flagval}{cflags} -c main.c",
        "directory": str(build),
    }]))
    return build


def self_test() -> int:
    """Feed the gate known-bad pairs and require each to be rejected.

    The project's harness rule: a gate that cannot fail is not evidence. Every case below is a
    real way this gate could have been fooled, and the fourth is the one that motivated checks 3
    and 4 -- a flag that is passed to the compiler and read by nobody.
    """
    base_kconfig = {"CONFIG_ARM_MPU": "y", "CONFIG_STACK_CANARIES": "<not set>"}
    ok = True
    tmp = Path(tempfile.mkdtemp(prefix="cuberange-gate-selftest."))
    try:
        app = tmp / "app"
        app.mkdir()
        (app / "main.c").write_text("#if CUBERANGE_TEST_FLAG\nint hardened;\n#endif\n")

        def mk(root: Path, name: str, kconfig: dict, cache: dict, elf: bytes,
               cflags: str = "", home: Path | None = None) -> Path:
            """_fake_build with the source tree defaulting to the app the case declares.

            Without this every case below would be rejected by check 0 (provenance) instead of by
            the check it was written to exercise, and the self-test would report six passes while
            testing one thing. That is the precise shape of vacuous success this file exists to
            refuse, and adding check 0 introduced it - the cases are ordered before the check they
            test, so a new first check silently captures all of them.
            """
            return _fake_build(root, name, kconfig, cache, elf, cflags,
                               home=str(home if home is not None else app))

        def pair(build_v: str, build_h: str) -> dict:
            return {"id": "SELFTEST", "app": str(app), "flag": "CUBERANGE_TEST_FLAG",
                    "vuln": {"value": "0", "build": build_v},
                    "hard": {"value": "1", "build": build_h}}

        # Each case carries the substring its rejection must contain. Rejection alone is not
        # enough: adding the provenance check made every case below rejectable by check 0, and a
        # self-test that only asks "was it refused?" would have reported six passes while
        # exercising one check. The reason is the assertion.
        cases: list[tuple[str, dict, Path, str]] = []

        # (a) Kconfig differs: a protection was turned off to make the exercise work.
        root = tmp / "a"
        mk(root, "v", base_kconfig, {"CUBERANGE_TEST_FLAG": "0"}, b"\x01vuln")
        mk(root, "h", {**base_kconfig, "CONFIG_ARM_MPU": "<not set>"},
                    {"CUBERANGE_TEST_FLAG": "1"}, b"\x02hard")
        cases.append(("Kconfig differs between the pair", pair("v", "h"), root,
                      "different Kconfig symbols"))

        # (b) A second project flag moved as well: "one flag" is not true.
        root = tmp / "b"
        mk(root, "v", base_kconfig,
                    {"CUBERANGE_TEST_FLAG": "0", "CUBERANGE_OTHER": "0"}, b"\x01vuln")
        mk(root, "h", base_kconfig,
                    {"CUBERANGE_TEST_FLAG": "1", "CUBERANGE_OTHER": "1"}, b"\x02hard")
        cases.append(("a second CUBERANGE_ flag also differs", pair("v", "h"), root,
                      "expected exactly"))

        # (c) Identical ELFs: the flag reached CMake but changed no code.
        root = tmp / "c"
        mk(root, "v", base_kconfig, {"CUBERANGE_TEST_FLAG": "0"}, b"same")
        mk(root, "h", base_kconfig, {"CUBERANGE_TEST_FLAG": "1"}, b"same")
        cases.append(("the two builds produced identical ELFs", pair("v", "h"), root,
                      "same ELF"))

        # (d) No source mentions the flag: it is defined and never read.
        root = tmp / "d"
        empty_app = tmp / "empty_app"
        empty_app.mkdir()
        (empty_app / "main.c").write_text("int main(void) { return 0; }\n")
        mk(root, "v", base_kconfig, {"CUBERANGE_TEST_FLAG": "0"}, b"\x01vuln", home=empty_app)
        mk(root, "h", base_kconfig, {"CUBERANGE_TEST_FLAG": "1"}, b"\x02hard", home=empty_app)
        p = pair("v", "h")
        p["app"] = str(empty_app)
        cases.append(("no source references the flag", p, root, "no source under"))

        # (e2) The vulnerability manufactured through compiler options rather than the flag.
        #      Kconfig matches, one cache variable moves, the ELFs differ, the flag is referenced -
        #      checks 1 to 4 all pass, and the "vulnerable" build simply had a protection removed.
        root = tmp / "e2"
        mk(root, "v", base_kconfig, {"CUBERANGE_TEST_FLAG": "0"}, b"\x01vuln",
                    cflags=" -fno-stack-protector")
        mk(root, "h", base_kconfig, {"CUBERANGE_TEST_FLAG": "1"}, b"\x02hard")
        cases.append(("a protection was removed via compiler options", pair("v", "h"), root,
                      "compiled differently beyond"))

        # (e) A missing build directory must be a failure, not an absence of evidence. This is the
        #     exact shape of the bug that made an earlier probe.sh report PASS for everything.
        root = tmp / "e"
        mk(root, "v", base_kconfig, {"CUBERANGE_TEST_FLAG": "0"}, b"\x01vuln")
        cases.append(("the mitigated build directory is missing", pair("v", "absent"), root,
                      "build the pair first"))

        # (g) Somebody else's artifacts. Two checkouts of this repository on one machine used to
        #     share /tmp/cuberange, so this is not hypothetical - it happened, and what the gate
        #     reported was four unexplained cache variables rather than the truth.
        root = tmp / "g"
        mk(root, "v", base_kconfig, {"CUBERANGE_TEST_FLAG": "0"}, b"\x01vuln",
           home=Path("/home/someone/another-checkout/firmware/apps/x"))
        mk(root, "h", base_kconfig, {"CUBERANGE_TEST_FLAG": "1"}, b"\x02hard")
        cases.append(("the artifacts came from a different checkout", pair("v", "h"), root,
                      "was produced from"))

        print(f"{BOLD}== self-test: these must all be REJECTED, each for its own reason =={RESET}")
        for label, p, root, expect in cases:
            try:
                check_pair(p, root)
            except GateError as exc:
                first = str(exc).splitlines()[0]
                if expect in str(exc):
                    print(f"  {GREEN}PASS{RESET}  rejected: {label}")
                    print(f"        {YELLOW}{first[:150]}{RESET}")
                else:
                    ok = False
                    print(f"  {RED}FAIL{RESET}  rejected for the WRONG reason: {label}")
                    print(f"        expected a message containing {expect!r}, got: {first[:120]}")
            else:
                ok = False
                print(f"  {RED}FAIL{RESET}  ACCEPTED a bad pair: {label}")

        # (f) And the honest case must still pass, or the gate is merely a rejector.
        root = tmp / "f"
        mk(root, "v", base_kconfig, {"CUBERANGE_TEST_FLAG": "0"}, b"\x01vuln")
        mk(root, "h", base_kconfig, {"CUBERANGE_TEST_FLAG": "1"}, b"\x02hard")
        try:
            check_pair(pair("v", "h"), root)
            print(f"  {GREEN}PASS{RESET}  accepted: a correctly built pair")
        except GateError as exc:
            ok = False
            print(f"  {RED}FAIL{RESET}  rejected a good pair: {exc}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if ok:
        print(f"{GREEN}self-test passed: the gate detects every known-bad pair and accepts a good one{RESET}")
        return 0
    print(f"{RED}self-test FAILED: the gate is not evidence{RESET}")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matrix", type=Path, default=REPO / "firmware-matrix.yml")
    ap.add_argument("--out", type=Path,
                    default=out_dir(),
                    help="directory holding the west build trees (default: $OUT, else this "
                         "checkout's own directory under /tmp - see src/cuberange/paths.py)")
    ap.add_argument("--self-test", action="store_true",
                    help="feed the gate known-bad pairs and require it to reject them")
    args = ap.parse_args()
    return self_test() if args.self_test else run(args.matrix, args.out)


if __name__ == "__main__":
    sys.exit(main())
