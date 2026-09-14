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

# EX-D01's only difference. EX-G04 gave the ground the ability to HEAR a refusal; nobody
# authenticated it, so anybody could speak. This signs the report with the same trailer the
# telecommands carry.
#   west build ... -- -DCUBERANGE_OBC_SIGN_REPORTS=1
set(CUBERANGE_OBC_SIGN_REPORTS 0 CACHE STRING
    "put an authentication trailer on the reports the spacecraft sends to the ground")

# EX-L02's only difference. A counter gap on a lossy link says "you did not hear something" and
# not "somebody took it"; the way to tell them apart is to ask again. This keeps the last eight
# reports verbatim and sends one back on request.
#   west build ... -- -DCUBERANGE_OBC_REPORT_STORE=1
set(CUBERANGE_OBC_REPORT_STORE 0 CACHE STRING
    "keep recent reports and resend one when the ground asks (PUS 15 subset)")

# EX-L03's only difference. A gap in a counter needs two reports to sit between; a spacecraft that
# only speaks when spoken to gives an attacker who denies everything a quiet link and no gap at
# all. A beacon makes silence mean something, which is why nearly every real spacecraft has one.
#   west build ... -- -DCUBERANGE_OBC_BEACON=1
set(CUBERANGE_OBC_BEACON 0 CACHE STRING
    "send an unsolicited housekeeping report on a timer (PUS 3,25 subset)")

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
  CUBERANGE_OBC_REQUIRE_PUS_AUTH=${CUBERANGE_OBC_REQUIRE_PUS_AUTH}
  CUBERANGE_OBC_SIGN_REPORTS=${CUBERANGE_OBC_SIGN_REPORTS}
  CUBERANGE_OBC_REPORT_STORE=${CUBERANGE_OBC_REPORT_STORE}
  CUBERANGE_OBC_BEACON=${CUBERANGE_OBC_BEACON})
