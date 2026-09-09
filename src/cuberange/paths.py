"""Where builds go, in one place.

Every checkout of this repository used to default to the SAME build directory, `/tmp/cuberange`.
On a machine with one working copy that is invisible. On a machine with two it is not a nuisance,
it is a correctness failure: two `west build -p always` runs write the same `build-obc/`, and
whichever finished last owns it. The gate then compares one checkout's vulnerable image against
another checkout's mitigated image and reports something that looks like a defect in the firmware.

That happened here, and the report was four unexplained cache variables rather than "these are
somebody else's artifacts" - which is why `config_diff_gate.py` now checks CMAKE_HOME_DIRECTORY as
its FIRST check, and why the default below carries the checkout's path in its name.

The Makefile computes the same string independently, because a Make variable cannot call into
Python without making every `make help` pay for an interpreter start. `test_paths.py` parses the
Makefile and asserts the two derivations agree, exactly as `test_identity.py` does for
`identity.cmake` - two derivations of one fact are a liability unless something compares them.

$OUT still overrides everything. It is how CI pins a path and how you point a run at somebody
else's artifacts on purpose.
"""
import hashlib
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Length of the path digest in the directory name. Eight hex characters is 32 bits: enough that
#: two checkouts on one machine will not collide, short enough that the name stays readable in the
#: error messages that quote it.
TAG_LEN = 8


def default_out(repo: Path = REPO) -> Path:
    """The build directory for a given checkout, unique to its absolute path.

    Readable half first so the directory is recognisable in `ls /tmp`, digest second so two
    checkouts that share a basename - the common case, since they are clones of one repository -
    still get different directories.
    """
    repo = Path(repo).resolve()
    tag = hashlib.md5(str(repo).encode()).hexdigest()[:TAG_LEN]
    return Path(f"/tmp/cuberange-{repo.name}-{tag}")


def out_dir() -> Path:
    """$OUT if it is set and non-empty, else this checkout's default.

    An empty $OUT is treated as unset rather than as the path "", because `Path("")` is `.` and a
    build target that then writes into the source tree - or a clean target that removes it - is a
    far worse outcome than ignoring a variable somebody exported by accident.
    """
    return Path(os.environ["OUT"]) if os.environ.get("OUT") else default_out()
