# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_comm)

include(${CMAKE_CURRENT_LIST_DIR}/../../common/identity.cmake)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c
                          ../../common/cuberange_sdls.c)
target_include_directories(app PRIVATE ../../common)
# The ONLY difference between the vulnerable and mitigated COMM images.
#   west build ... -- -DCUBERANGE_COMM_ANTIREPLAY=1
set(CUBERANGE_COMM_ANTIREPLAY 0 CACHE STRING "reject replayed TC frames")

# EX-G03's only difference. Independent of the flag above: the anti-replay must be ON in both
# halves of this pair, because what differs is WHERE the counter lives, not whether there is one.
#   west build ... -- -DCUBERANGE_COMM_ANTIREPLAY=1 -DCUBERANGE_COMM_ANTIREPLAY_PER_VC=1
set(CUBERANGE_COMM_ANTIREPLAY_PER_VC 0 CACHE STRING
    "keep the replay counter per virtual channel, as CCSDS does, instead of per link")

# EX-X01's crosslink. NOT a vuln/hardened pair - it is a second CAN interface, and both halves of
# EX-X01's pair use the same crosslink COMM. The flag exists because enabling it changes every
# other exercise's COMM otherwise, and those exercises rest on pairs that compare builds.
#
# It travels with two files that are applied by the build command, not by this list:
#   -DEXTRA_DTC_OVERLAY_FILE=.../crosslink.overlay   enables fdcan2
#   -DEXTRA_CONF_FILE=.../crosslink.conf             a second CSP RX thread, and heap for it
# Setting this without them compiles code that calls DEVICE_DT_GET on a disabled node.
set(CUBERANGE_COMM_CROSSLINK 0 CACHE STRING
    "join fdcan2 to the constellation's shared crosslink bus as a second CSP interface")

# EX-S01's SDLS verification. Travels with sdls.conf (mbedTLS and its heap), applied by the build
# command, for the same reason the crosslink does: setting this alone compiles calls into a library
# the build has not enabled.
set(CUBERANGE_COMM_SDLS 0 CACHE STRING
    "verify a CCSDS 355.0-B-2 authentication MAC on every uplink frame")

# EX-U03's only difference. Every link-layer control above ends in a printk on a console nobody
# off the spacecraft can read: the forged frame is refused, the replay is refused, the attack
# fails completely and leaves no record anywhere a human will look. This counts them and sends
# the counts to the OBC, which carries them in the housekeeping beacon.
#   west build ... -- -DCUBERANGE_COMM_LINK_STATS=1
set(CUBERANGE_COMM_LINK_STATS 0 CACHE STRING
    "count frames received and refused at the link layer and report them to the OBC")

# EX-S03's only difference. The SA table exists in both halves and both carry both keys; what
# differs is whether the RETIRED association is deactivated. A rotation that activates the new SA
# and leaves the old one operational has moved the operator onto new key material and has retired
# nothing, and the operator has no way to find out - their new commands work, and so do their old.
#   west build ... -- -DCUBERANGE_COMM_SA_DEACTIVATION=1
set(CUBERANGE_COMM_SA_DEACTIVATION 0 CACHE STRING
    "mark the retired Security Association non-operational so frames on its SPI are refused")

# EX-S04's only difference. Both halves carry all three Security Associations and the STOP_SA
# directive handler - SA management by telecommand is the feature, not the flaw. What differs is
# whether a directive is AUTHORISED: OFF, any frame that verifies retires any SA it names, so a
# partner holding SPI 11 switches the operator's SPI 9 off with a valid MAC; ON, a directive may
# retire an SA only when it authenticated under an SA with the SAME owner. SA_DEACTIVATION is held
# at 0 in both halves of this pair, so every SA boots operational and the retirement happens on the
# wire.
#   west build ... -- -DCUBERANGE_COMM_SA_MGMT_AUTHORITY=1
set(CUBERANGE_COMM_SA_MGMT_AUTHORITY 0 CACHE STRING
    "require a STOP_SA directive to share the target SA's owner before it may retire it")

# EX-S05's only difference. Both halves retire SAs by telecommand (authority held off in this pair,
# so a partner's retirement lands and there is something to attribute). What differs is whether COMM
# REPORTS who retired which SA to the OBC, which puts it on the signed beacon: OFF, the retirement
# is silent to the ground and a theft looks like the operator's own rotation; ON, the beacon names
# the target SPI and the retiring SPI. It rides EX-D01's report signing rather than adding its own.
#   west build ... -- -DCUBERANGE_COMM_SA_MGMT_REPORT=1
set(CUBERANGE_COMM_SA_MGMT_REPORT 0 CACHE STRING
    "report the target and requester SPI of the last SA retirement to the OBC for the beacon")

# EX-S06's only difference. Both halves keep the SA-management ring and report to the OBC
# (SA_MGMT_REPORT held on in this pair, authority held off, so the retirements land and there is a
# sequence to report). What differs is the transmit DEPTH: OFF, COMM sends only the last recorded
# event - the single snapshot EX-S05 sent, which a later retirement of the same SPI overwrites and
# reattributes; ON, COMM sends the whole log oldest-first, so a theft that a legitimate retirement
# overwrote is still on the beacon. It rides EX-S05's report path, one field widened to a list.
#   west build ... -- -DCUBERANGE_COMM_SA_MGMT_HISTORY=1
set(CUBERANGE_COMM_SA_MGMT_HISTORY 0 CACHE STRING
    "report the whole SA-management event log to the OBC, not only the last retirement")

target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_COMM_ANTIREPLAY=${CUBERANGE_COMM_ANTIREPLAY}
  CUBERANGE_COMM_ANTIREPLAY_PER_VC=${CUBERANGE_COMM_ANTIREPLAY_PER_VC}
  CUBERANGE_COMM_CROSSLINK=${CUBERANGE_COMM_CROSSLINK}
  CUBERANGE_COMM_SDLS=${CUBERANGE_COMM_SDLS}
  CUBERANGE_COMM_LINK_STATS=${CUBERANGE_COMM_LINK_STATS}
  CUBERANGE_COMM_SA_DEACTIVATION=${CUBERANGE_COMM_SA_DEACTIVATION}
  CUBERANGE_COMM_SA_MGMT_AUTHORITY=${CUBERANGE_COMM_SA_MGMT_AUTHORITY}
  CUBERANGE_COMM_SA_MGMT_REPORT=${CUBERANGE_COMM_SA_MGMT_REPORT}
  CUBERANGE_COMM_SA_MGMT_HISTORY=${CUBERANGE_COMM_SA_MGMT_HISTORY})
