# SPDX-License-Identifier: Apache-2.0
cmake_minimum_required(VERSION 3.20)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(cuberange_comm)

target_sources(app PRIVATE src/main.c ../../common/cuberange_proto.c)
target_include_directories(app PRIVATE ../../common)
target_link_libraries(app PRIVATE csp)
