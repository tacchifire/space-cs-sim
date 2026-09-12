# Spacecraft identity, as build parameters.
#
# A second satellite in the same emulation needs its own CSP addresses and its own SCID, or the two
# are indistinguishable on the wire and on the bus. Making that a build parameter rather than a
# source edit means one source tree still produces every node of every spacecraft, which is the
# property that lets `tools/config_diff_gate.py` prove a vulnerable and a mitigated image differ by
# exactly one flag.
#
# CSP v1 addresses are FIVE BITS - src/cuberange/proto/csp.py masks with 0x1F and the CFP CAN
# identifier uses the same width - so 0..31, and there is no room for a wide per-satellite stride.
# Eight per spacecraft gives four spacecraft, which is more than the measured node budget allows
# anyway (the design's per-node cost stops being measured past six machines).
#
# Satellite 0 keeps the numbers every existing scenario, exercise and write-up names. Do not
# renumber it.
#
#   satellite 0    OBC 1   EPS 2   ADCS 4   COMM 5    SCID 0x0A9   APID 0x0A9
#   satellite 1    OBC 9   EPS 10  ADCS 12  COMM 13   SCID 0x0AA   APID 0x0AA
#
# These are NOT prefixed CUBERANGE_ on purpose. The pair gate asserts that no CUBERANGE_* cache
# variable other than the exercise's own flag differs between a vulnerable and a mitigated build,
# and identity is set identically on both halves of a pair - but keeping the namespace clean means
# a future identity change can never be mistaken for a weakened build.

set(CUBERANGE_SAT_INDEX 0 CACHE STRING "which spacecraft this image belongs to")

# Both bounds. Only the upper one existed at first, so CUBERANGE_SAT_INDEX=-1 produced OBC -7,
# EPS -6, ADCS -4 and COMM -3: values that reach libcsp through an implicit conversion rather than
# a configuration error.
if(CUBERANGE_SAT_INDEX LESS 0 OR CUBERANGE_SAT_INDEX GREATER 3)
  message(FATAL_ERROR
    "satellite index ${CUBERANGE_SAT_INDEX} is outside 0..3. CSP v1 addresses are five bits wide, "
    "so eight per spacecraft leaves room for four spacecraft and no more.")
endif()

math(EXPR _cr_base "${CUBERANGE_SAT_INDEX} * 8")
math(EXPR _cr_obc  "${_cr_base} + 1")
math(EXPR _cr_eps  "${_cr_base} + 2")
math(EXPR _cr_adcs "${_cr_base} + 4")
math(EXPR _cr_comm "${_cr_base} + 5")
# The crosslink terminal, and the reason it is a SEPARATE address rather than the COMM's own.
#
# libcsp's split horizon asks "is the OUTGOING interface's address inside the INCOMING interface's
# subnet?" (csp_io.c, three times; csp_iflist.c:15). A router with two interfaces that both carry
# the node's address answers yes to that for every netmask, so it forwards NOTHING - silently, with
# no error and no counter. Measured here: the packet arrives on fdcan2 and stops.
#
# Giving the crosslink attachment its own address makes the two interfaces distinguishable. Offset
# 6 is free: 1, 2, 4 and 5 are the node roles and 7 is the subnet broadcast address.
math(EXPR _cr_xlink "${_cr_base} + 6")
math(EXPR _cr_scid "169 + ${CUBERANGE_SAT_INDEX}")     # 169 = 0x0A9

# Ground stations. The PUS TC secondary header carries a 16-bit source id, and until EX-G02 the
# spacecraft received it, logged it, echoed it into the report's destination id - and never used
# it to decide anything. These are the two identities the on-board authority table is written
# against. They do not vary per spacecraft: a ground station is a ground station whichever
# satellite it is talking to.
set(_cr_gs_primary 66)      # 0x0042, the id every existing scenario and write-up already uses
set(_cr_gs_backup  67)      # 0x0043

target_compile_definitions(app PRIVATE
  GROUND_PRIMARY_ID=${_cr_gs_primary}
  GROUND_BACKUP_ID=${_cr_gs_backup}
  OBC_ADDR=${_cr_obc}
  ADDR_EPS=${_cr_eps}
  EPS_ADDR=${_cr_eps}
  ADCS_ADDR=${_cr_adcs}
  COMM_ADDR=${_cr_comm}
  XLINK_ADDR=${_cr_xlink}
  CR_SCID=${_cr_scid}
  OBC_APID=${_cr_scid})

message(STATUS
  "CubeRange satellite ${CUBERANGE_SAT_INDEX}: OBC=${_cr_obc} EPS=${_cr_eps} "
  "ADCS=${_cr_adcs} COMM=${_cr_comm} XLINK=${_cr_xlink} SCID=${_cr_scid}")
