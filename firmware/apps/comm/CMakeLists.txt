# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_comm)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c)
target_include_directories(app PRIVATE ../../common)
# The ONLY difference between the vulnerable and mitigated COMM images.
#   west build ... -- -DCUBERANGE_COMM_ANTIREPLAY=1
set(CUBERANGE_COMM_ANTIREPLAY 0 CACHE STRING "reject replayed TC frames")

target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_COMM_ANTIREPLAY=${CUBERANGE_COMM_ANTIREPLAY})
