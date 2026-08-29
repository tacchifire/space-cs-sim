# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_eps)

# The ONLY difference between the vulnerable and mitigated images. Everything else - prj.conf,
# board, overlay, sources, compiler flags - is identical, so a reviewer can see that the exercise
# is not manufactured by weakening the platform.
#   west build ... -- -DCUBERANGE_EPS_REQUIRE_AUTH=1
set(CUBERANGE_EPS_REQUIRE_AUTH 0 CACHE STRING "reject unauthenticated power commands")

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c)
target_include_directories(app PRIVATE ../../common)
target_link_libraries(app PRIVATE csp)
target_compile_definitions(app PRIVATE
  CUBERANGE_EPS_REQUIRE_AUTH=${CUBERANGE_EPS_REQUIRE_AUTH})
