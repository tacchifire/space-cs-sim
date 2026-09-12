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

target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_COMM_ANTIREPLAY=${CUBERANGE_COMM_ANTIREPLAY}
  CUBERANGE_COMM_ANTIREPLAY_PER_VC=${CUBERANGE_COMM_ANTIREPLAY_PER_VC}
  CUBERANGE_COMM_CROSSLINK=${CUBERANGE_COMM_CROSSLINK}
  CUBERANGE_COMM_SDLS=${CUBERANGE_COMM_SDLS})
