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
OUT        ?= /tmp/cuberange
BOARD      ?= nucleo_h753zi

APP_CSP_PING := firmware/apps/csp_ping

.PHONY: help probe firmware demo spike clean toolchain

help:
	@echo "make toolchain  - install Zephyr v4.1.0 + SDK (no root, ~7.6 GB on disk)"
	@echo "make probe      - verify every Renode capability the design depends on"
	@echo "make firmware   - build the two CSP nodes"
	@echo "make demo       - run the two nodes over a CAN hub and show the CSP pings"
	@echo "make spike      - host-side proof: raw CAN injection + External Control"

toolchain:
	./tools/setup-toolchain.sh $(ZEPHYR_WS) $(HOME)/zephyr-sdk

probe:
	RENODE_DIR=$(RENODE_DIR) ./tools/renode-probe/probe.sh

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
	cd $(RENODE_DIR) && ./renode --disable-xwt --console --plain --hide-analyzers --hide-log \
	  -e '$$n1=@$(OUT)/build-n1/zephyr/zephyr.elf' \
	  -e '$$n2=@$(OUT)/build-n2/zephyr/zephyr.elf' \
	  -e '$$n1uart=@$(OUT)/n1.uart' \
	  -e '$$n2uart=@$(OUT)/n2.uart' \
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

demo-p0: firmware-p0
	PYTHONPATH=src RENODE_DIR=$(RENODE_DIR) OUT=$(OUT) \
	    python3 -m pytest tests/e2e/test_p0_roundtrip.py -v

# Everything that can be checked without a human looking at it.
check: probe
	PYTHONPATH=src python3 -m pytest tests/pytest -q
	$(MAKE) -C tests/native test
	$(MAKE) demo-p0
