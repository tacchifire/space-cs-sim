# CubeRange
#
# Prerequisites, both installed outside the repo:
#   Renode 1.16.1 portable  -> $(RENODE_DIR)
#   Zephyr v4.1.0 + SDK     -> ./tools/setup-toolchain.sh
#
# The environment file that setup-toolchain.sh writes is sourced by the firmware targets.

RENODE_DIR ?= $(HOME)/tools/renode_1.16.1-dotnet_portable
ZEPHYR_WS  ?= $(HOME)/zephyrproject
LIBCSP     ?= $(HOME)/libcsp
ENV        ?= $(HOME)/cuberange-env.sh
# Unique to this checkout. It used to be a bare /tmp/cuberange, which every clone of this
# repository shared: two working copies on one machine then wrote the same build-obc/ and the pair
# gate compared one checkout's image against the other's. src/cuberange/paths.py explains the
# incident and derives the identical string in Python; tests/pytest/test_paths.py parses this line
# and fails if the two ever disagree.
# Every Renode launch goes through here. Renode 1.16.1 binds the Monitor and every socket
# terminal to 0.0.0.0 and offers no way to name an address, and what those sockets accept is the
# exercise: unauthenticated telecommands and raw CAN frames. ISOLATE puts the run in a network
# namespace holding only loopback, so 0.0.0.0 is a promise about interfaces that do not exist.
#
# `python3 -m cuberange.safety.isolate --probe` says which backend works here and why the others
# do not. On a host where none does, set CUBERANGE_ALLOW_UNISOLATED=1 - deliberately, and knowing
# what it means.
ISOLATE = PYTHONPATH=src python3 -m cuberange.safety.isolate --

CUBERANGE_REPO := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
OUT        ?= /tmp/cuberange-$(notdir $(CUBERANGE_REPO))-$(shell printf '%s' '$(CUBERANGE_REPO)' | md5sum | cut -c1-8)
BOARD      ?= nucleo_h753zi

APP_CSP_PING := firmware/apps/csp_ping

# Every firmware target uses `west build -p always`, a pristine build, deliberately: an incremental
# build that silently reuses a stale object is exactly the kind of thing this project refuses to
# rely on. The cost is that a target which depends on firmware rebuilds it every time, and `check`
# runs four such targets - so it was building the same six images four times over and taking an
# hour and a half.
#
# NOFW=1 says "the images in $(OUT) were just built by the caller". `check` sets it after building
# them once. Running any target directly still rebuilds, which is the safe default.
FWDEP = $(if $(NOFW),,$(1))

.PHONY: help out probe firmware demo spike clean toolchain

help:
	@echo "make out        - print this checkout's build directory ($(OUT))"
	@echo "make toolchain  - install Zephyr v4.1.0 + SDK (no root, ~7.6 GB on disk)"
	@echo "make probe      - verify every Renode capability the design depends on"
	@echo "make firmware   - build the two CSP nodes"
	@echo "make firmware-g02 - EX-G02's pair: the OBC with and without the authority check"
	@echo "make demo       - run the two nodes over a CAN hub and show the CSP pings"
	@echo "make spike      - host-side proof: raw CAN injection + External Control"

# Where this checkout builds. Print it rather than asking anybody to recompute the digest by hand:
# the exercises' objdump and nm lines use $(shell make -s out) so they stay correct in a clone at
# any path.
out:
	@echo $(OUT)

toolchain:
	./tools/setup-toolchain.sh $(ZEPHYR_WS) $(HOME)/zephyr-sdk

probe:
	$(ISOLATE) env RENODE_DIR=$(RENODE_DIR) ./tools/renode-probe/probe.sh

# One source tree, one node role per build directory. The address is a CMake cache variable, so
# adding a node means adding a build line, not a source file.
firmware:
	@test -f $(ENV) || { echo "missing $(ENV) - run 'make toolchain' first"; exit 1; }
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-n1 $(APP_CSP_PING) -- \
	    -DCUBERANGE_NODE_ADDR=1 -DCUBERANGE_PEER_ADDR=2 -DCUBERANGE_CLIENT_ADDR=1
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-n2 $(APP_CSP_PING) -- \
	    -DCUBERANGE_NODE_ADDR=2 -DCUBERANGE_PEER_ADDR=1 -DCUBERANGE_CLIENT_ADDR=1
	@ls -l $(OUT)/build-n1/zephyr/zephyr.elf $(OUT)/build-n2/zephyr/zephyr.elf

demo:
	@test -f $(OUT)/build-n1/zephyr/zephyr.elf || { echo "run 'make firmware' first"; exit 1; }
	@mkdir -p $(OUT)
	@rm -f $(OUT)/n1.uart $(OUT)/n2.uart
	cd $(RENODE_DIR) && $(CURDIR:%=PYTHONPATH=%/src) python3 -m cuberange.safety.isolate -- \
	  ./renode --disable-xwt --console --plain --hide-analyzers --hide-log \
	  -e '$$out=@$(OUT)' \
	  -e '$$n1=@$(OUT)/build-n1/zephyr/zephyr.elf' \
	  -e '$$n2=@$(OUT)/build-n2/zephyr/zephyr.elf' \
	  -e '$$n1uart=@$(OUT)/n1.uart' \
	  -e '$$n2uart=@$(OUT)/n2.uart' \
	  -e '$$profile=@$(CURDIR)/scripts/profiles/interactive.resc' \
	  -e 'include @$(CURDIR)/scripts/multi-node/csp_ping.resc' \
	  -e 'emulation RunFor "8"' -e 'quit' < /dev/null > $(OUT)/renode.log 2>&1
	@echo "--- node 1 (client) ---"; cat $(OUT)/n1.uart
	@echo "--- node 2 (server) ---"; cat $(OUT)/n2.uart
	@grep -q 'ping 0 -> node 2 ok' $(OUT)/n1.uart \
	  && echo "PASS: CSP over CAN between two CubeRange nodes" \
	  || { echo "FAIL: no successful ping"; exit 1; }

spike:
	RENODE_DIR=$(RENODE_DIR) python3 tests/manual/spike_inject_and_observe.py

clean:
	rm -rf $(OUT)

# P0 node images. One source tree per role; the shared codec is compiled into each.
firmware-p0:
	@test -f $(ENV) || { echo "missing $(ENV) - run 'make toolchain' first"; exit 1; }
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-comm firmware/apps/comm
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc firmware/apps/obc
	@ls -l $(OUT)/build-comm/zephyr/zephyr.elf $(OUT)/build-obc/zephyr/zephyr.elf

demo-p0: $(call FWDEP,firmware-p0)
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_p0_roundtrip.py -v

# Everything that can be checked without a human looking at it.
#
# The final banner exists because a failing sub-target once hid inside 8500 lines of build output:
# make returned non-zero correctly, but a human skimming for "passed" saw only the parts that had.
# If you do not see CHECK PASSED on the last line, it did not pass.
check: probe
	@# The oracle tests are guarded by pytest.importorskip, so a missing spacepackets or crcmod
	@# removes the entire conformance layer and the run still ends in CHECK PASSED. A gate that
	@# silently drops its own evidence is not a gate - so require them here.
	@python3 -c "import spacepackets, crcmod" 2>/dev/null || { \
	    echo "the independent oracles are missing - pip install -r requirements.txt"; \
	    echo "(without them the CCSDS and CRC conformance tests skip silently)"; exit 1; }
	PYTHONPATH=src python3 -m pytest tests/pytest -q
	$(MAKE) -C tests/native test
	@# Build every image ONCE, then run the four targets that need them with NOFW=1. Each of those
	@# targets rebuilds on its own when run directly, which is what you want while editing; inside
	@# check it meant four pristine builds of the same six images.
	$(MAKE) firmware-all
	$(MAKE) firmware-sat1
	$(MAKE) demo-p0     NOFW=1
	$(MAKE) determinism NOFW=1
	$(MAKE) constellation NOFW=1
	$(MAKE) verify-all  NOFW=1
	$(MAKE) pair-gate   NOFW=1
	@echo
	@echo "================================================================"
	@echo "  CHECK PASSED - probe, codecs, native, round trip, determinism,"
	@echo "                 constellation, exercises, firmware-pair gate"
	@echo "================================================================"

# P0's last acceptance condition: thirty round trips in a row. Slow (~10 min) and excluded from
# `make check`, because a soak belongs on a schedule rather than in the edit loop.
soak-p0: firmware-p0
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_p0_soak.py -v -s

# EX-B01 images: EPS in both profiles plus the P0 pair. The two EPS builds differ by exactly one
# CMake cache variable, and a CI gate diffs their CONFIG_* symbol dumps to prove nothing else moved.
firmware-p1: firmware-p0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-eps-vuln firmware/apps/eps -- -DCUBERANGE_EPS_REQUIRE_AUTH=0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-eps-hard firmware/apps/eps -- -DCUBERANGE_EPS_REQUIRE_AUTH=1
	@ls -l $(OUT)/build-eps-vuln/zephyr/zephyr.elf $(OUT)/build-eps-hard/zephyr/zephyr.elf

# Run one exercise's verification, in both directions.
#   make verify EX=EX-B01-eps-killswitch
verify:
	@test -n "$(EX)" || { echo "usage: make verify EX=<exercise-dir>"; exit 1; }
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest exercises/$(EX)/verify_*.py -v

# Every exercise, both directions. This is the claim the product makes.
verify-all: $(call FWDEP,firmware-exl01 firmware-a01 firmware-f01 firmware-g01 firmware-g02)
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest exercises/*/verify_*.py -v

# EX-L01 adds a replay-hardened COMM alongside the P1 images.
firmware-exl01: firmware-p1
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-comm-hard firmware/apps/comm -- -DCUBERANGE_COMM_ANTIREPLAY=1
	@ls -l $(OUT)/build-comm-hard/zephyr/zephyr.elf

# The anti-strawman gate. CONTRIBUTING.md states the proof as a `diff` a contributor runs by hand,
# and this Makefile used to describe it as "a CI gate" that did not exist. It now exists, it reads
# firmware-matrix.yml, and it carries self-tests because a gate that cannot fail is not evidence.
#
# Four checks per pair, and the last two are the ones that matter: a misspelled cache variable
# produces two identical images and a perfectly clean Kconfig diff, so "nothing else moved" would
# pass while the mitigation was never compiled in.
.PHONY: pair-gate
pair-gate: $(call FWDEP,firmware-exl01 firmware-a01 firmware-f01)
	python3 tools/config_diff_gate.py --self-test
	OUT=$(OUT) python3 tools/config_diff_gate.py

# Rules G1 and G2 of the design's determinism section, reproduced rather than asserted. Runs the
# same scenario three times under the CI profile and requires the guest UART captures to be
# byte-identical. Measured here: 3/3 identical in 27 s.
.PHONY: determinism
determinism: $(call FWDEP,firmware-p0)
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_determinism.py -q

# EX-A01 images: the ADCS in both profiles. Needs the P0 pair and the hardened EPS as well, because
# the scenario runs the FIXED EPS - EX-A01 is about a subsystem nobody bounded, not one nobody
# authenticated, and running it against the broken EPS would let a learner solve it the EX-B01 way.
firmware-a01: firmware-p1
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-adcs-vuln firmware/apps/adcs -- -DCUBERANGE_ADCS_TORQUE_LIMIT=0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-adcs-hard firmware/apps/adcs -- -DCUBERANGE_ADCS_TORQUE_LIMIT=1
	@ls -l $(OUT)/build-adcs-vuln/zephyr/zephyr.elf $(OUT)/build-adcs-hard/zephyr/zephyr.elf

# EX-F01 images: the OBC in both profiles. build-obc is the vulnerable one, the same way build-comm
# is for EX-L01 - the default build of a node is the one with the flaw, so a learner who runs
# `make firmware-p0` and nothing else gets the exercise rather than the fix.
firmware-f01: firmware-p1
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-hard firmware/apps/obc -- -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1
	@ls -l $(OUT)/build-obc/zephyr/zephyr.elf $(OUT)/build-obc-hard/zephyr/zephyr.elf

# Every image, built once. The three leaf targets all chain through firmware-p1, and make runs a
# prerequisite once per invocation, so this is one pristine build of each of the seven images
# rather than the four rounds `check` used to do.
.PHONY: firmware-all
firmware-all: firmware-exl01 firmware-a01 firmware-f01 firmware-g02
	@echo "all node images built:"
	@ls -1 $(OUT)/build-*/zephyr/zephyr.elf

# Run one exercise's scenario and leave it up, so a learner can send their own packets at it. Three
# exercise READMEs have told people to type this since before the target existed; it does now.
#
# Foreground and unsupervised on purpose: you are meant to watch it, and Ctrl-C is the stop button.
# Anything automated goes through src/cuberange/renode/supervisor.py instead, which is where the
# watchdog and the RSS ceiling live.
.PHONY: exercise
# The one target a student runs by hand, and the one nothing in `make check` exercises. It was
# broken and hung: the scenarios stopped defaulting $out and this recipe never passed it, so
# Renode aborted the -e chain at the first LoadELF, never reached the trailing command, and sat at
# its Monitor prompt with an EMPTY log - the exact failure mode CLAUDE.md documents, in the first
# command anybody types. tests/pytest/test_paths.py now checks every Renode launch in this file.
#
# Not supervised, deliberately: a student watches this one and stops it with Ctrl-C, and a
# watchdog that killed it after four minutes would be a defect rather than a guard. It IS
# isolated, because a range whose containment depends on which target you ran is not contained.
exercise:
	@test -n "$(EX)" || { echo "usage: make exercise EX=<exercise-dir>"; exit 1; }
	@test -d exercises/$(EX) || { echo "no such exercise: exercises/$(EX)"; \
	    echo "available:"; ls -1 exercises; exit 1; }
	@test -f $(OUT)/build-comm/zephyr/zephyr.elf || { \
	    echo "no firmware in $(OUT) - run 'make firmware-all' first"; exit 1; }
	@echo "starting exercises/$(EX) - Ctrl-C to stop"
	@echo "  space link  localhost:$(shell PYTHONPATH=src python3 -c 'from cuberange import ports; print(ports.link(0))')"
	@echo "  monitor     localhost:$(shell PYTHONPATH=src python3 -c 'from cuberange import ports; print(ports.monitor())')"
	@echo "  injector    localhost:$(shell PYTHONPATH=src python3 -c 'from cuberange import ports; print(ports.injector(0))')"
	@echo "  images from $(OUT)"
	cd $(RENODE_DIR) && $(CURDIR:%=PYTHONPATH=%/src) python3 -m cuberange.safety.isolate -- \
	  ./renode --disable-xwt --plain --hide-analyzers \
	  --port $(shell PYTHONPATH=src python3 -c 'from cuberange import ports; print(ports.monitor())') \
	  -e '$$injector=@$(CURDIR)/attacker/TcpCanInjector.cs' \
	  -e '$$profile=@$(CURDIR)/scripts/profiles/interactive.resc' \
	  -e '$$out=@$(OUT)' \
	  -e 'include @$(CURDIR)/exercises/$(EX)/scenario.resc' \
	  -e 'start'

# Satellite 1: the same four node roles, built with a different identity.
#
# One source tree, one build per (role, spacecraft). firmware/common/identity.cmake derives the CSP
# addresses and the SCID from CUBERANGE_SAT_INDEX, so a second spacecraft is a build parameter and
# not a source edit - which is what keeps the pair gate's "differ by exactly one flag" claim true
# for every image.
#
# The vulnerable profiles are used here on purpose: satellite 1 exists so a scenario can show an
# attack landing on one spacecraft while the other keeps working, and a fully hardened second
# satellite would have nothing to show.
.PHONY: firmware-sat1
firmware-sat1:
	@test -f $(ENV) || { echo "missing $(ENV) - run 'make toolchain' first"; exit 1; }
	for role in comm obc eps adcs; do \
	    . $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	        -d $(OUT)/build-$$role-sat1 firmware/apps/$$role -- -DCUBERANGE_SAT_INDEX=1 || exit 1; \
	done
	@ls -l $(OUT)/build-*-sat1/zephyr/zephyr.elf

# Two spacecraft in one emulation: eight machines, two CAN hubs, two space links, one Monitor.
#
# Measured on an 8-core x86-64 host: eight nodes boot and the four assertions run in 17 s, peak RSS
# 688 MB, and 20 virtual seconds cost 4.16 s of emulation (4.81x real time). That last figure is
# NOT comparable with the design's 2.34x four-node number: these nodes are idle after boot, where
# that measurement had CAN traffic, and the host was under load 8.9 at the time. RSS is the honest
# comparison, and it is in line with the design's +50-120 MB per node.
.PHONY: constellation
constellation: $(call FWDEP,firmware-all firmware-sat1)
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_constellation.py -v

# EX-G01 needs the HARDENED OBC and EPS: the exercise is about a command that every spacecraft-side
# control honours correctly, so the spacecraft-side controls all have to be present.
firmware-g01: firmware-f01
	@ls -l $(OUT)/build-obc-hard/zephyr/zephyr.elf $(OUT)/build-eps-hard/zephyr/zephyr.elf

# EX-G02's pair. Both halves carry EX-F01's length check ON: the exercise is about a memory-safe
# service 8 that still executes for anyone, and leaving the overflow in would let a reader
# conclude the overflow was the problem. The two differ only in CUBERANGE_OBC_REQUIRE_AUTHORITY,
# which is what tools/config_diff_gate.py checks.
firmware-g02:
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-g02-vuln firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-g02-hard firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1
	@ls -l $(OUT)/build-obc-g02-vuln/zephyr/zephyr.elf $(OUT)/build-obc-g02-hard/zephyr/zephyr.elf

# Rebuild the independent oracles and regenerate the golden vectors.
#
# Not part of `make check`: it clones two external projects and needs a network, and the vectors it
# produces are committed precisely so that the conformance layer does NOT depend on either. Run it
# when a vector needs to change, and let the diff be the review.
#
# Neither oracle is vendored. libcsp is MIT and CryptoLib is NOSA 1.3, and the licence policy
# (README section 11) permits both as external oracles and not as dependencies.
.PHONY: oracles golden
oracles:
	tools/oracles/build.sh $(ORACLE_DIR)

ORACLE_DIR ?= /tmp/cuberange-oracles
golden: oracles
	cd $(ORACLE_DIR) && python3 $(CURDIR)/tools/gen_golden.py $(CURDIR)/tests/golden
	@echo
	@echo "Review the diff before committing - these vectors are what the codecs are checked against."
	@git -C $(CURDIR) diff --stat tests/golden || true
