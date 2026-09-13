# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_obc)

# The ONLY difference between the vulnerable and mitigated images. Everything else - prj.conf,
# board, overlay, sources, compiler flags - is identical, and tools/config_diff_gate.py proves it.
#   west build ... -- -DCUBERANGE_OBC_PUS8_LENGTH_CHECK=1
set(CUBERANGE_OBC_PUS8_LENGTH_CHECK 0 CACHE STRING "bound the PUS 8 argument copy")

# EX-G02's only difference. Independent of the one above: each exercise's pair holds the other
# flag equal, which is what lets tools/config_diff_gate.py say "exactly one differs" about both.
#   west build ... -- -DCUBERANGE_OBC_REQUIRE_AUTHORITY=1
set(CUBERANGE_OBC_REQUIRE_AUTHORITY 0 CACHE STRING
    "check the telecommand's source id against an on-board authority table")

# EX-G04's only difference. A refusal the ground cannot hear is indistinguishable from a command
# that never arrived, which is what EX-G02 and EX-G03 both end by saying.
#   west build ... -- -DCUBERANGE_OBC_VERIFY_REPORTS=1
set(CUBERANGE_OBC_VERIFY_REPORTS 0 CACHE STRING
    "send PUS 1,2 acceptance failure reports when a telecommand is refused")

# EX-X01's only difference. The crosslink gives a telecommand a second way in, and the source id
# in its secondary header is a field the sender writes - so the authority table above authorises
# whoever fills it in. This binds the claim to the CSP address the packet actually arrived from.
#   west build ... -- -DCUBERANGE_OBC_CROSSLINK_ORIGIN=1
set(CUBERANGE_OBC_CROSSLINK_ORIGIN 0 CACHE STRING
    "a packet claiming a ground station's source id must have arrived from this spacecraft's COMM")

# EX-S02's only difference. The one control in this range that is not attached to a path: the MAC
# is on the telecommand, so the same octets verify off the space link, off the crosslink, or off
# the internal bus. Travels with pus_auth.conf (mbedTLS and the stack it needs).
#   west build ... -- -DCUBERANGE_OBC_REQUIRE_PUS_AUTH=1
set(CUBERANGE_OBC_REQUIRE_PUS_AUTH 0 CACHE STRING
    "verify a MAC carried inside the telecommand itself, whatever link it arrived on")

include(${CMAKE_CURRENT_LIST_DIR}/../../common/identity.cmake)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c
                          ../../common/cuberange_pus_auth.c)
target_include_directories(app PRIVATE ../../common)
target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_OBC_PUS8_LENGTH_CHECK=${CUBERANGE_OBC_PUS8_LENGTH_CHECK}
  CUBERANGE_OBC_REQUIRE_AUTHORITY=${CUBERANGE_OBC_REQUIRE_AUTHORITY}
  CUBERANGE_OBC_VERIFY_REPORTS=${CUBERANGE_OBC_VERIFY_REPORTS}
  CUBERANGE_OBC_CROSSLINK_ORIGIN=${CUBERANGE_OBC_CROSSLINK_ORIGIN}
  CUBERANGE_OBC_REQUIRE_PUS_AUTH=${CUBERANGE_OBC_REQUIRE_PUS_AUTH})
