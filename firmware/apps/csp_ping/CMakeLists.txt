# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_csp_ping)

# Node identity is a build-time parameter, so one source tree produces every node role.
# Pass with:  west build ... -- -DCUBERANGE_NODE_ADDR=2
set(CUBERANGE_NODE_ADDR   1 CACHE STRING "CSP address of this node")
set(CUBERANGE_PEER_ADDR   2 CACHE STRING "CSP address this node pings")
set(CUBERANGE_CLIENT_ADDR 1 CACHE STRING "which address runs the ping client")

target_sources(app PRIVATE src/main.c)
target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_NODE_ADDR=${CUBERANGE_NODE_ADDR}
  CUBERANGE_PEER_ADDR=${CUBERANGE_PEER_ADDR}
  CUBERANGE_CLIENT_ADDR=${CUBERANGE_CLIENT_ADDR}
)
