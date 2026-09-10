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

include(${CMAKE_CURRENT_LIST_DIR}/../../common/identity.cmake)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c)
target_include_directories(app PRIVATE ../../common)
target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_OBC_PUS8_LENGTH_CHECK=${CUBERANGE_OBC_PUS8_LENGTH_CHECK}
  CUBERANGE_OBC_REQUIRE_AUTHORITY=${CUBERANGE_OBC_REQUIRE_AUTHORITY})
