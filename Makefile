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

.PHONY: help out syllabus gs channel ground-stations fleet probe firmware demo spike clean toolchain

help:
	@echo "make out        - print this checkout's build directory ($(OUT))"
	@echo "make syllabus   - the order the exercises are meant to be taken in"
	@echo "make ground-stations - two ground station nodes, one spacecraft, one channel"
	@echo "make fleet SATS=4 - every spacecraft the addressing allows, 16 nodes"
	@echo "make gs STATION=backup - a console to sit at (needs make channel running)"
	@echo "make toolchain  - install Zephyr v4.1.0 + SDK (no root, ~7.6 GB on disk)"
	@echo "make probe      - verify every Renode capability the design depends on"
	@echo "make firmware   - build the two CSP nodes"
	@echo "make firmware-g02 - EX-G02's pair: the OBC with and without the authority check"
	@echo "make firmware-g03 - EX-G03's pair: the replay counter per link or per channel"
	@echo "make firmware-g04 - EX-G04's pair: a refusal the ground can hear, or cannot"
	@echo "make demo       - run the two nodes over a CAN hub and show the CSP pings"
	@echo "make spike      - host-side proof: raw CAN injection + External Control"

# Where this checkout builds. Print it rather than asking anybody to recompute the digest by hand:
# the exercises' objdump and nm lines use $(shell make -s out) so they stay correct in a clone at
# any path.
out:
	@echo $(OUT)

# The order the exercises were written to be taken in, derived from their own front matter
# rather than written out beside them. Six correct prerequisite edges existed for weeks and
# nothing read them: a curriculum that is true and unreachable.
syllabus:
	@python3 tools/syllabus.py

# Two ground station NODES against one spacecraft, through the channel. Renode's socket terminal
# serves one client, so without the channel accepting several this cannot exist - the range had
# two ground station identities and no second console for a second student.
ground-stations: $(call FWDEP,firmware-g02)
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_ground_stations.py -v

# A console to sit at, while a scenario is already running in another terminal:
#   term 1:  make exercise EX=EX-G02-unauthorized-authority
#   term 2:  make channel
#   term 3:  make gs STATION=backup
# The backup station refuses what its own matrix forbids; add OVERRIDE=1 to send it anyway and
# watch a spacecraft that never checked obey.
gs:
	@test -n "$(STATION)" || { echo "usage: make gs STATION=primary|backup [OVERRIDE=1]"; exit 1; }
	$(ISOLATE) env PYTHONPATH=src OUT=$(OUT) python3 -m cuberange.gs.node \
	    --station $(STATION) --port $$(PYTHONPATH=src python3 -c \
	      'from cuberange import ports; print(ports.channel(0))') \
	    $(if $(OVERRIDE),--override,)

# The channel a station attaches to, in front of satellite 0's link.
channel:
	$(ISOLATE) env PYTHONPATH=src OUT=$(OUT) python3 -c \
	    'from cuberange.channel.link_channel import LinkChannel; from cuberange import ports; \
	     import time; c = LinkChannel(ports.channel(0), sat_port=ports.link(0)).start(); \
	     print("channel up on", ports.channel(0), "-> satellite", ports.link(0)); \
	     time.sleep(86400)'

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
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 tests/manual/spike_inject_and_observe.py

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
	$(MAKE) fleet NOFW=1
	$(MAKE) ground-stations NOFW=1
	$(MAKE) verify-all  NOFW=1
	$(MAKE) pair-gate   NOFW=1
	$(MAKE) sdls        NOFW=1
	$(MAKE) workflow    NOFW=1
	@echo
	@echo "================================================================"
	@echo "  CHECK PASSED - probe, codecs, native, round trip, determinism,"
	@echo "                 constellation, exercises, firmware-pair gate"
	@echo "================================================================"

# P0's last acceptance condition: thirty round trips in a row. Slow (~10 min) and excluded from
# `make check`, because a soak belongs on a schedule rather than in the edit loop.
# -m slow, because pytest.ini sets `addopts = -m "not slow"` and the only test in this file is
# marked slow. Without the override the run deselects everything, exits 5 and reports "1
# deselected" - which is what this target did for its whole existence, while CLAUDE.md advertised
# it with a runtime. tests/pytest/test_paths.py now fails if a target selects nothing.
soak-p0: $(call FWDEP,firmware-p0)
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_p0_soak.py -v -s -m slow

# EX-B01 images: EPS in both profiles plus the P0 pair. The two EPS builds differ by exactly one
# CMake cache variable, and a CI gate diffs their CONFIG_* symbol dumps to prove nothing else moved.
firmware-p1: firmware-p0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-eps-vuln firmware/apps/eps -- -DCUBERANGE_EPS_REQUIRE_AUTH=0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-eps-hard firmware/apps/eps -- -DCUBERANGE_EPS_REQUIRE_AUTH=1
	@ls -l $(OUT)/build-eps-vuln/zephyr/zephyr.elf $(OUT)/build-eps-hard/zephyr/zephyr.elf

# An authenticated uplink, on the spacecraft.
#
# Four assertions, and the first is that AES-256-GCM gets the published NIST answer on the target -
# which the build succeeding does not tell you. See the note in sdls.conf about the stack.
sdls: $(call FWDEP,firmware-sdls firmware-p1 firmware-exl01)
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_sdls_uplink.py -v

# The reader's path, run the way a reader runs it.
#
# NOT wrapped in $(ISOLATE): the namespace this is about is the one `make exercise` creates, and
# entering another first would test a namespace no reader enters. (Wrapping would no longer break
# it - isolate runs the command directly when it is already inside a loopback-only namespace - but
# it would be the wrong namespace.)
workflow: $(call FWDEP,firmware-all)
	env PYTHONPATH=src OUT=$(OUT) RENODE_DIR=$(RENODE_DIR) \
	    python3 -m pytest tests/e2e/test_documented_workflow.py -v

# Run one exercise's verification, in both directions.
#   make verify EX=EX-B01-eps-killswitch
verify:
	@test -n "$(EX)" || { echo "usage: make verify EX=<exercise-dir>"; exit 1; }
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest exercises/$(EX)/verify_*.py -v

# Every exercise, both directions. This is the claim the product makes.
verify-all: $(call FWDEP,firmware-exl01 firmware-a01 firmware-f01 firmware-g01 firmware-g02 firmware-g03 firmware-g04 firmware-x01 firmware-s01 firmware-s02 firmware-d01)
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
firmware-all: firmware-exl01 firmware-a01 firmware-f01 firmware-g02 firmware-g03 firmware-g04 firmware-x01 firmware-sdls firmware-s01 firmware-s02 firmware-d01
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
# Run an exercise, and be INSIDE it.
#
#   make exercise EX=EX-B01-eps-killswitch
#   make exercise EX=EX-B01-eps-killswitch RUN='python3 exercises/EX-B01-eps-killswitch/solve.py'
#
# The shell this drops you into is inside the range's network namespace, and that is not a
# convenience. ISOLATE runs Renode in a namespace containing only loopback (SAFE_USE.md), a new
# namespace per invocation, so a solver started from ANOTHER terminal cannot reach the space link
# or the injector - measured: ConnectionRefusedError, every time.
#
# Every exercise README told the reader to do exactly that: `make exercise` here, `python3
# solve.py` there. Fourteen files, both languages, and it had not worked since isolation landed on
# 2026-09-10. Nothing caught it because no gate ran the reader's path - `make verify` launches
# Renode from inside pytest, which is already in the namespace, so the tested path and the
# documented path were different paths.
#
# RUN= is that gate's way in: it runs one command inside the namespace instead of a shell, which
# is what tests/e2e/test_documented_workflow.py does.
exercise:
	@test -n "$(EX)" || { echo "usage: make exercise EX=<exercise-dir> [RUN='<command>']"; exit 1; }
	@test -d exercises/$(EX) || { echo "no such exercise: exercises/$(EX)"; \
	    echo "available:"; ls -1 exercises; exit 1; }
	@test -f $(OUT)/build-comm/zephyr/zephyr.elf || { \
	    echo "no firmware in $(OUT) - run 'make firmware-all' first"; exit 1; }
	$(ISOLATE) env PYTHONPATH=$(CURDIR)/src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    CUBERANGE_EX='$(EX)' CUBERANGE_RUN='$(RUN)' \
	    bash $(CURDIR)/tools/exercise.sh

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
	@$(MAKE) firmware-sat SAT=1

# Any spacecraft by index. identity.cmake refuses an index past 3 - CSP v1 addresses are five
# bits, eight per spacecraft - so this cannot quietly build a satellite that collides with
# another one's nodes.
firmware-sat:
	@test -n "$(SAT)" || { echo "usage: make firmware-sat SAT=<0..3>"; exit 1; }
	@test -f $(ENV) || { echo "missing $(ENV) - run 'make toolchain' first"; exit 1; }
	for role in comm obc eps adcs; do \
	    . $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	        -d $(OUT)/build-$$role-sat$(SAT) firmware/apps/$$role -- \
	        -DCUBERANGE_SAT_INDEX=$(SAT) || exit 1; \
	done
	@ls -l $(OUT)/build-*-sat$(SAT)/zephyr/zephyr.elf

# Two spacecraft in one emulation: eight machines, two CAN hubs, two space links, one Monitor.
#
# Measured on an 8-core x86-64 host: eight nodes boot and the four assertions run in 17 s, peak RSS
# 688 MB, and 20 virtual seconds cost 4.16 s of emulation (4.81x real time). That last figure is
# NOT comparable with the design's 2.34x four-node number: these nodes are idle after boot, where
# that measurement had CAN traffic, and the host was under load 8.9 at the time. RSS is the honest
# comparison, and it is in line with the design's +50-120 MB per node.
.PHONY: constellation
# SATS=<n> runs more spacecraft, up to the four CSP v1's five-bit addressing allows. The
# scenario for anything but the committed two is generated on the spot from cuberange.identity,
# because sixteen nodes hand-written is 280 lines in which the only thing that varies is an index
# - and the repetition is where a hub name gets reused and both spacecraft quietly share a bus.
SATS ?= 2

# The generated scenario, run. SATS=2 in the gate because that needs no images beyond the ones
# `check` already builds, and what it adds over `make constellation` is coverage of the GENERATOR
# - that what tools/gen_constellation.py emits actually boots.
#
# SATS=4 is the full fleet, sixteen nodes, and needs `make firmware-sat SAT=2` and SAT=3 first.
# Measured on an eight-core x86-64 host: 16/16 booted in 10.0s at 1.66-2.52x real time, 907 MB
# peak. That is recorded rather than gated - eight more images is eight more minutes on every
# `make check`, and the design already carries measurements it does not gate.
fleet: $(call FWDEP,firmware-all firmware-sat1)
	$(ISOLATE) env PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    CUBERANGE_SATS=$(SATS) python3 -m pytest tests/e2e/test_constellation_full.py -v -s

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
# EX-G03's pair. The anti-replay is ON in both: what differs is whether its counter is per link
# or per virtual channel.
# EX-G04's pair. EX-G02's authority check is ON in both: what differs is whether the refusal
# reaches the ground as PUS 1,2, or only the console nobody off the spacecraft can read.
firmware-d01:
	# EX-D01's pair. Both halves have EX-G04's reports ON and EX-S02's command authentication ON,
	# so the only thing that differs is whether the report going the other way carries a trailer.
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-d01-vuln firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1 \
	    -DCUBERANGE_OBC_VERIFY_REPORTS=1 -DCUBERANGE_OBC_REQUIRE_PUS_AUTH=1 \
	    -DCUBERANGE_OBC_SIGN_REPORTS=0 \
	    -DEXTRA_CONF_FILE=$(CUBERANGE_REPO)/firmware/apps/obc/pus_auth.conf
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-d01-hard firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1 \
	    -DCUBERANGE_OBC_VERIFY_REPORTS=1 -DCUBERANGE_OBC_REQUIRE_PUS_AUTH=1 \
	    -DCUBERANGE_OBC_SIGN_REPORTS=1 \
	    -DEXTRA_CONF_FILE=$(CUBERANGE_REPO)/firmware/apps/obc/pus_auth.conf
	@ls -l $(OUT)/build-obc-d01-vuln/zephyr/zephyr.elf $(OUT)/build-obc-d01-hard/zephyr/zephyr.elf

firmware-s02:
	# EX-S02's pair. The vulnerable half is EX-X01's hardened OBC - authority table ON, refusal
	# reports ON - so the thing that differs is only whether the telecommand carries its own MAC.
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-s02-vuln firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1 \
	    -DCUBERANGE_OBC_VERIFY_REPORTS=1 -DCUBERANGE_OBC_REQUIRE_PUS_AUTH=0 \
	    -DEXTRA_CONF_FILE=$(CUBERANGE_REPO)/firmware/apps/obc/pus_auth.conf
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-s02-hard firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1 \
	    -DCUBERANGE_OBC_VERIFY_REPORTS=1 -DCUBERANGE_OBC_REQUIRE_PUS_AUTH=1 \
	    -DEXTRA_CONF_FILE=$(CUBERANGE_REPO)/firmware/apps/obc/pus_auth.conf
	@ls -l $(OUT)/build-obc-s02-vuln/zephyr/zephyr.elf $(OUT)/build-obc-s02-hard/zephyr/zephyr.elf

firmware-s01:
	# COMM with BOTH the crosslink and SDLS, on both spacecraft. That combination is the exercise:
	# the uplink is authenticated and the crosslink is not, because SDLS sits on the transfer-frame
	# layer and the crosslink carries CSP. Nothing about the two flags interacts - which is the
	# thing EX-S01 asks the student to notice.
	for sat in 0 1; do \
	    . $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	        -d $(OUT)/build-comm-s01-sat$$sat firmware/apps/comm -- \
	        -DCUBERANGE_SAT_INDEX=$$sat \
	        -DCUBERANGE_COMM_SDLS=1 -DCUBERANGE_COMM_CROSSLINK=1 \
	        -DCUBERANGE_COMM_ANTIREPLAY=1 -DCUBERANGE_COMM_ANTIREPLAY_PER_VC=1 \
	        -DEXTRA_DTC_OVERLAY_FILE=$(CUBERANGE_REPO)/firmware/apps/comm/crosslink.overlay \
	        -DEXTRA_CONF_FILE="$(CUBERANGE_REPO)/firmware/apps/comm/crosslink.conf;$(CUBERANGE_REPO)/firmware/apps/comm/sdls.conf" \
	        || exit 1; \
	done
	@ls -l $(OUT)/build-comm-s01-sat0/zephyr/zephyr.elf $(OUT)/build-comm-s01-sat1/zephyr/zephyr.elf

firmware-sdls:
	# COMM that speaks AUTHENTICATED framing and nothing else. Not a pair: there is no "SDLS off"
	# half, because an SDLS frame and a plain frame are two formats rather than one format with a
	# flag - the payload starts in a different place. The exercise that uses this compares it
	# against the EXISTING plain builds, which is a comparison between formats and is the point.
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-comm-sdls firmware/apps/comm -- \
	    -DCUBERANGE_COMM_SDLS=1 \
	    -DEXTRA_CONF_FILE=$(CUBERANGE_REPO)/firmware/apps/comm/sdls.conf
	@ls -l $(OUT)/build-comm-sdls/zephyr/zephyr.elf

firmware-x01:
	# COMM with the crosslink, on BOTH spacecraft, and with every link-layer defence this range
	# has turned ON: EX-L01's anti-replay and EX-G03's per-VC sequence numbers. That is
	# deliberate. EX-X01 is not about a COMM with its guard down - it is about WHERE those guards
	# are. They live in on_tc_frame, the space-link deframer, and a CSP packet arriving on the
	# crosslink never reaches it.
	#
	# The crosslink is a devicetree change and a Kconfig change, both applied through EXTRA_*
	# files rather than the app's own boards/ overlay and prj.conf, so that every other
	# exercise's COMM keeps the build it has. Those exercises rest on pairs that compare builds.
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-comm-xlink firmware/apps/comm -- \
	    -DCUBERANGE_COMM_ANTIREPLAY=1 -DCUBERANGE_COMM_ANTIREPLAY_PER_VC=1 \
	    -DCUBERANGE_COMM_CROSSLINK=1 \
	    -DEXTRA_DTC_OVERLAY_FILE=$(CUBERANGE_REPO)/firmware/apps/comm/crosslink.overlay \
	    -DEXTRA_CONF_FILE=$(CUBERANGE_REPO)/firmware/apps/comm/crosslink.conf
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-comm-xlink-sat1 firmware/apps/comm -- \
	    -DCUBERANGE_SAT_INDEX=1 \
	    -DCUBERANGE_COMM_ANTIREPLAY=1 -DCUBERANGE_COMM_ANTIREPLAY_PER_VC=1 \
	    -DCUBERANGE_COMM_CROSSLINK=1 \
	    -DEXTRA_DTC_OVERLAY_FILE=$(CUBERANGE_REPO)/firmware/apps/comm/crosslink.overlay \
	    -DEXTRA_CONF_FILE=$(CUBERANGE_REPO)/firmware/apps/comm/crosslink.conf
	# The pair. REQUIRE_AUTHORITY is ON in BOTH halves - EX-G02's defence is present and working
	# in the vulnerable build, and it does not help. That is the exercise.
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-x01-vuln firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1 \
	    -DCUBERANGE_OBC_VERIFY_REPORTS=1 -DCUBERANGE_OBC_CROSSLINK_ORIGIN=0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-x01-hard firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1 \
	    -DCUBERANGE_OBC_VERIFY_REPORTS=1 -DCUBERANGE_OBC_CROSSLINK_ORIGIN=1
	@ls -l $(OUT)/build-comm-xlink/zephyr/zephyr.elf \
	       $(OUT)/build-comm-xlink-sat1/zephyr/zephyr.elf \
	       $(OUT)/build-obc-x01-vuln/zephyr/zephyr.elf $(OUT)/build-obc-x01-hard/zephyr/zephyr.elf

firmware-g04:
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-g04-vuln firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1 \
	    -DCUBERANGE_OBC_VERIFY_REPORTS=0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-obc-g04-hard firmware/apps/obc -- \
	    -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1 -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1 \
	    -DCUBERANGE_OBC_VERIFY_REPORTS=1
	@ls -l $(OUT)/build-obc-g04-vuln/zephyr/zephyr.elf $(OUT)/build-obc-g04-hard/zephyr/zephyr.elf

firmware-g03:
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-comm-g03-vuln firmware/apps/comm -- \
	    -DCUBERANGE_COMM_ANTIREPLAY=1 -DCUBERANGE_COMM_ANTIREPLAY_PER_VC=0
	. $(ENV) && ZEPHYR_EXTRA_MODULES=$(LIBCSP) west build -p always -b $(BOARD) \
	    -d $(OUT)/build-comm-g03-hard firmware/apps/comm -- \
	    -DCUBERANGE_COMM_ANTIREPLAY=1 -DCUBERANGE_COMM_ANTIREPLAY_PER_VC=1
	@ls -l $(OUT)/build-comm-g03-vuln/zephyr/zephyr.elf $(OUT)/build-comm-g03-hard/zephyr/zephyr.elf

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
