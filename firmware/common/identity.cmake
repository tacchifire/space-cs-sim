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
math(EXPR _cr_scid "169 + ${CUBERANGE_SAT_INDEX}")     # 169 = 0x0A9

target_compile_definitions(app PRIVATE
  OBC_ADDR=${_cr_obc}
  ADDR_EPS=${_cr_eps}
  EPS_ADDR=${_cr_eps}
  ADCS_ADDR=${_cr_adcs}
  COMM_ADDR=${_cr_comm}
  CR_SCID=${_cr_scid}
  OBC_APID=${_cr_scid})

message(STATUS
  "CubeRange satellite ${CUBERANGE_SAT_INDEX}: OBC=${_cr_obc} EPS=${_cr_eps} "
  "ADCS=${_cr_adcs} COMM=${_cr_comm} SCID=${_cr_scid}")
