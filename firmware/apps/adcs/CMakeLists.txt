# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_adcs)

# The ONLY difference between the vulnerable and mitigated images. Everything else - prj.conf,
# board, overlay, sources, compiler flags - is identical, and tools/config_diff_gate.py proves it
# rather than asking a reviewer to take it on trust.
#   west build ... -- -DCUBERANGE_ADCS_TORQUE_LIMIT=1
set(CUBERANGE_ADCS_TORQUE_LIMIT 0 CACHE STRING "reject out-of-range torque commands")

include(${CMAKE_CURRENT_LIST_DIR}/../../common/identity.cmake)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c)
target_include_directories(app PRIVATE ../../common)
target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_ADCS_TORQUE_LIMIT=${CUBERANGE_ADCS_TORQUE_LIMIT})
