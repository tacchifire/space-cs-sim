# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_comm)

include(${CMAKE_CURRENT_LIST_DIR}/../../common/identity.cmake)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c)
target_include_directories(app PRIVATE ../../common)
# The ONLY difference between the vulnerable and mitigated COMM images.
#   west build ... -- -DCUBERANGE_COMM_ANTIREPLAY=1
set(CUBERANGE_COMM_ANTIREPLAY 0 CACHE STRING "reject replayed TC frames")

# EX-G03's only difference. Independent of the flag above: the anti-replay must be ON in both
# halves of this pair, because what differs is WHERE the counter lives, not whether there is one.
#   west build ... -- -DCUBERANGE_COMM_ANTIREPLAY=1 -DCUBERANGE_COMM_ANTIREPLAY_PER_VC=1
set(CUBERANGE_COMM_ANTIREPLAY_PER_VC 0 CACHE STRING
    "keep the replay counter per virtual channel, as CCSDS does, instead of per link")

target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_COMM_ANTIREPLAY=${CUBERANGE_COMM_ANTIREPLAY}
  CUBERANGE_COMM_ANTIREPLAY_PER_VC=${CUBERANGE_COMM_ANTIREPLAY_PER_VC})
