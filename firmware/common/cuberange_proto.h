/*
 * CubeRange wire codec, shared by every node.
 *
 * Deliberately free of Zephyr and board dependencies so it builds on the host for unit tests and
 * fuzzing. Everything is caller-allocated; nothing here allocates or blocks.
 *
 * The Python side of the same wire format lives in src/cuberange/proto/. The two are written
 * independently and diffed byte-for-byte by tests/pytest/test_c_matches_python.py, because two
 * self-written codecs that share a mistake round-trip against each other perfectly.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#ifndef CUBERANGE_PROTO_H
#define CUBERANGE_PROTO_H

#include <stddef.h>
#include <stdint.h>

#define CR_ASM_0 0x1a
#define CR_ASM_1 0xcf
#define CR_ASM_2 0xfc
#define CR_ASM_3 0x1d

/* Spacecraft identity. Overridable so a second satellite is a build parameter rather than a
 * source edit: with both spacecraft emitting 0x0A9 their frames are wire-indistinguishable, and a
 * ground station connected to one would accept the other's telemetry without a word. See
 * firmware/common/identity.cmake and src/cuberange/proto/frame.py, which parameterises the same
 * pair on the host side. */
#ifndef CR_SCID
#define CR_SCID           0x0A9
#endif
#ifndef CR_VCID
#define CR_VCID           0
#endif
#define CR_TC_HEADER_LEN  5
#define CR_TM_HEADER_LEN  6
#define CR_FECF_LEN       2
#define CR_MAX_FRAME_LEN  1024

/* CRC-16/IBM-3740: poly 0x1021, init 0xFFFF, no reflection, xorout 0. CCSDS 132.0-B-3 s4.1.6.2.2.
 * This is NOT the XMODEM or KERMIT variant, both of which are also called "CCITT". Catalogue check
 * value for "123456789" is 0x29B1. */
uint16_t cr_crc16(const uint8_t *data, size_t len);

/* Return the frame length written to `out`, or -1 if it does not fit. */
int cr_encode_tc_frame(uint8_t *out, size_t out_cap,
		       const uint8_t *payload, size_t len, uint8_t seq);
int cr_encode_tm_frame(uint8_t *out, size_t out_cap,
		       const uint8_t *payload, size_t len, uint8_t mc, uint8_t vc);

/* Return 0 on success. `payload` points into `frame`; it is not copied. */
int cr_decode_tc_frame(const uint8_t *frame, size_t len, uint8_t *seq,
		       const uint8_t **payload, size_t *payload_len);

/* The virtual channel a TC frame arrived on: CCSDS 232.0-B-4 4.1.2.5, six bits in octet 2.
 *
 * Additive rather than another out-parameter on cr_decode_tc_frame, whose signature is called
 * from several places and byte-checked against the Python codec. Returns 0xFF for a frame too
 * short to have the field, which is not a valid six-bit VCID and so cannot be mistaken for one.
 *
 * COMM needs this because the standard maintains the frame sequence number PER VIRTUAL CHANNEL
 * (COP-1's FARM state is per VC), and this implementation kept one counter for the whole link -
 * which works exactly as long as there is one ground station. */
uint8_t cr_tc_frame_vcid(const uint8_t *frame, size_t len);
int cr_decode_tm_frame(const uint8_t *frame, size_t len, uint8_t *mc, uint8_t *vc,
		       const uint8_t **payload, size_t *payload_len);

/* ASM + u16 big-endian length + frame. Returns the total written, or 0 if it does not fit.
 * This is CubeRange lab framing, not a CCSDS CLTU (which is 231.0-B-4 and starts EB90). */
size_t cr_wrap(uint8_t *out, size_t out_cap, const uint8_t *frame, size_t len);

typedef void (*cr_frame_cb)(const uint8_t *frame, size_t len, void *ctx);

typedef struct {
	uint8_t buf[CR_MAX_FRAME_LEN + 8];
	size_t used;
} cr_deframer_t;

void cr_deframer_init(cr_deframer_t *d);

/* Feed a chunk; `cb` fires once per complete frame. Never fails on bad input - it resynchronises
 * on the next ASM, because a link that saw garbage must keep working. Returns the frame count. */
int cr_deframer_feed(cr_deframer_t *d, const uint8_t *chunk, size_t len,
		     cr_frame_cb cb, void *ctx);

#endif /* CUBERANGE_PROTO_H */
