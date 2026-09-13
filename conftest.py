"""Repository-wide pytest behaviour: a run in which nothing ran is a failure.

pytest exits 0 when every test it collected was skipped, and `make verify EX=...` then prints
"5 skipped" and succeeds. That is the failure this repository keeps finding under a new name -
W42 (a fuzzer that did not exist while the docs said it ran), W46 (a documented workflow no gate
walked) - and it happened again while EX-S02 was being written: a copied path pointed at a build
directory that does not exist, every test skipped on the `pytestmark` guard, and `make verify`
reported success.

A skip is a legitimate answer to "this needs firmware you have not built". It is not a legitimate
answer to ALL of it, because then the gate measured nothing and said so in a way that reads like
a pass.
"""
import pytest


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None or session.config.option.collectonly:
        return
    stats = reporter.stats
    ran = sum(len(stats.get(k, [])) for k in ("passed", "failed", "error"))
    skipped = len(stats.get("skipped", []))
    if ran == 0 and skipped > 0:
        reasons = []
        for rep in stats["skipped"][:3]:
            longrepr = getattr(rep, "longrepr", None)
            reasons.append(str(longrepr[2]) if isinstance(longrepr, tuple) and len(longrepr) > 2
                           else str(longrepr))
        reporter.write_line("")
        reporter.write_line(
            f"EVERY ONE of {skipped} collected tests was skipped, so this run measured nothing. "
            f"Exiting non-zero: a gate that reports success without running is the thing this "
            f"repository's section 16 is a list of.", red=True)
        for r in reasons:
            reporter.write_line(f"    first reasons: {r}", red=True)
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
