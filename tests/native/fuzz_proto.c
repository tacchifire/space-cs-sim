/* A fuzzer for the codec that parses attacker-controlled bytes.
 *
 * SECURITY.md said this file "is fuzzed and sanitised". Neither was true: test_proto.c's own
 * header said it "can be fuzzed LATER", the Makefile said "later fuzzed on the host", and
 * `make check` ran the plain build and not the asan one. A document describing a verification
 * mechanism that does not exist is the failure the project's first rule is about, and it had
 * three of them (design section 16, W23/W24/W26). This is the mechanism.
 *
 * NOT COVERAGE-GUIDED, and the distinction matters enough to put at the top. libFuzzer is not
 * available in this toolchain - `cc` here is zig cc, which does not ship the fuzzer runtime, and
 * afl, honggfuzz and radamsa are not installed either (all four checked, not assumed). So this is
 * a seeded random campaign: cheap, deterministic, reproducible from its seed, and strictly weaker
 * than a guided fuzzer at finding deep paths. It finds what a random walk finds.
 *
 * WHAT IT CHECKS, beyond not crashing. ASAN and UBSAN catch the memory errors, and a decoder can
 * be memory-safe and still lie: report a payload that runs past the buffer it was given, or a
 * length that disagrees with the frame it came from. Every successful decode is checked against
 * the input it was handed, so "it did not crash" is the floor rather than the whole test.
 *
 *   make -C tests/native fuzz            # 200k iterations, seed 1
 *   make -C tests/native fuzz ITERS=5000000 SEED=$RANDOM
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cuberange_proto.h"

static uint64_t rng_state;

static uint64_t rnd(void)
{
	rng_state ^= rng_state << 13;
	rng_state ^= rng_state >> 7;
	rng_state ^= rng_state << 17;
	return rng_state;
}

static size_t rnd_below(size_t n)
{
	return n ? (size_t)(rnd() % n) : 0;
}

/* Outcomes, reported at the end. A campaign that only ever rejects has explored nothing, and one
 * that only ever accepts is not being given malformed input - either way the numbers say so
 * rather than a green line hiding it. */
static unsigned long accepted_tc, rejected_tc, accepted_tm, rejected_tm, frames_deframed;

static int failures;

#define CHECK(cond, msg) do { \
	if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, msg); failures++; } \
} while (0)

/* What the decoder PROMISES about a frame it accepted, checked against the frame rather than
 * against the decoder's own arithmetic.
 *
 * The first version of this only asserted internal consistency - that payload_len plus the header
 * and FECF came to len - which holds however wrong the decoder is, because payload_len is
 * computed from len. Deleting the length check and deleting the CRC check both went undetected
 * through 200000 iterations. "It did not crash" is a floor, not a test, and a fuzzer whose
 * invariants restate the implementation is checking that the code does what the code does.
 *
 * These two are the contract. CCSDS 232.0-B-4 4.1.2.7: the length field is the total octets minus
 * one. CCSDS 132.0-B-3: the CRC over a frame INCLUDING its FECF is zero, which is how a receiver
 * checks rather than by recomputing. A decoder that accepts a frame violating either has accepted
 * something a real spacecraft would not.
 */
static void check_tc_claims(const uint8_t *frame, size_t len,
			    const uint8_t *payload, size_t payload_len)
{
	CHECK(payload >= frame && payload <= frame + len,
	      "decoded payload starts outside the frame it came from");
	CHECK(payload + payload_len <= frame + len,
	      "decoded payload runs past the end of the frame");
	CHECK(payload_len + CR_TC_HEADER_LEN + CR_FECF_LEN == len,
	      "decoded payload length disagrees with the frame length");

	size_t declared = (size_t)(((frame[2] & 0x03) << 8) | frame[3]) + 1;

	CHECK(declared == len,
	      "accepted a frame whose declared length is not its actual length");
	CHECK(cr_crc16(frame, len) == 0x0000,
	      "accepted a frame whose FECF residue is not zero");
}

static void on_frame(const uint8_t *frame, size_t len, void *ctx)
{
	(void)ctx;
	frames_deframed++;
	CHECK(len <= CR_MAX_FRAME_LEN, "deframer emitted a frame longer than the maximum");
	/* Whatever comes out of the deframer goes straight back into the decoders, because that is
	 * what COMM does with it. */
	uint8_t seq;
	const uint8_t *payload;
	size_t payload_len;

	if (cr_decode_tc_frame(frame, len, &seq, &payload, &payload_len) == 0) {
		check_tc_claims(frame, len, payload, payload_len);
	}
	uint8_t vcid = cr_tc_frame_vcid(frame, len);

	CHECK(vcid <= 63 || vcid == 0xFF, "VCID accessor returned a value wider than six bits");
}

/* Four input shapes. Pure noise finds the length-field handling; an UNMUTATED valid frame is what
 * gets far enough in to exercise the accept path at all; a mutated one finds the CRC path,
 * because it gets far enough to be rejected for the right reason; and a valid frame with its
 * declared length rewritten finds the disagreements between fields.
 *
 * The unmutated shape was missing from the first version, and the accept rate was 48 in 200000.
 * A fuzzer that almost never gets past the CRC is fuzzing the CRC. */
static size_t make_input(uint8_t *buf, size_t cap)
{
	unsigned shape = (unsigned)(rnd() % 4);
	size_t len;

	if (shape == 0) {
		len = rnd_below(cap);
		for (size_t i = 0; i < len; i++) {
			buf[i] = (uint8_t)rnd();
		}
		return len;
	}

	uint8_t payload[64];
	size_t payload_len = rnd_below(sizeof(payload));

	for (size_t i = 0; i < payload_len; i++) {
		payload[i] = (uint8_t)rnd();
	}
	int n = (rnd() & 1)
		? cr_encode_tc_frame(buf, cap, payload, payload_len, (uint8_t)rnd())
		: cr_encode_tm_frame(buf, cap, payload, payload_len, (uint8_t)rnd(), (uint8_t)rnd());
	if (n <= 0) {
		return 0;
	}
	len = (size_t)n;

	if (shape == 1) {
		return len;                            /* valid, untouched */
	}
	if (shape == 2) {
		size_t flips = 1 + rnd_below(3);

		for (size_t i = 0; i < flips; i++) {
			buf[rnd_below(len)] ^= (uint8_t)(1u << (rnd() % 8));
		}
	} else {
		/* Rewrite the declared length, which is the field a decoder most easily believes. */
		if (len > 4) {
			buf[2] = (uint8_t)rnd();
			buf[3] = (uint8_t)rnd();
		}
	}
	return len;
}

int main(int argc, char **argv)
{
	unsigned long iters = (argc > 1) ? strtoul(argv[1], NULL, 10) : 200000UL;
	uint64_t seed = (argc > 2) ? strtoull(argv[2], NULL, 10) : 1ULL;

	rng_state = seed ? seed : 1ULL;
	printf("fuzzing cuberange_proto: %lu iterations, seed %llu\n",
	       iters, (unsigned long long)seed);

	uint8_t buf[CR_MAX_FRAME_LEN + 32];
	cr_deframer_t deframer;

	cr_deframer_init(&deframer);

	for (unsigned long i = 0; i < iters; i++) {
		size_t len = make_input(buf, sizeof(buf));

		uint8_t seq, mc, vc;
		const uint8_t *payload;
		size_t payload_len;

		if (cr_decode_tc_frame(buf, len, &seq, &payload, &payload_len) == 0) {
			accepted_tc++;
			check_tc_claims(buf, len, payload, payload_len);
		} else {
			rejected_tc++;
		}

		if (cr_decode_tm_frame(buf, len, &mc, &vc, &payload, &payload_len) == 0) {
			accepted_tm++;
			CHECK(payload >= buf && payload + payload_len <= buf + len,
			      "decoded TM payload is outside the frame it came from");
			CHECK(cr_crc16(buf, len) == 0x0000,
			      "accepted a TM frame whose FECF residue is not zero");
			CHECK(payload_len + CR_TM_HEADER_LEN + CR_FECF_LEN == len,
			      "decoded TM payload length disagrees with the frame length");
		} else {
			rejected_tm++;
		}

		/* The VCID field is six bits (CCSDS 232.0-B-4 4.1.2.5), and 0xFF is the sentinel for
		 * a frame too short to hold it - a value no six-bit field can take, which is why it
		 * was chosen. Anything else means the accessor is reading neighbouring bits: the
		 * frame length shares octet 2, and reading two bits too many would silently put a
		 * ground station on somebody else's virtual channel. */
		uint8_t vcid = cr_tc_frame_vcid(buf, len);

		CHECK(vcid <= 63 || vcid == 0xFF, "VCID accessor returned a value wider than six bits");
		if (len >= CR_TC_HEADER_LEN) {
			CHECK(vcid != 0xFF, "a frame long enough for the field reported the sentinel");
		}

		/* The deframer takes the LINK's bytes, not a bare frame: ASM plus a length plus the
		 * frame, which is what cr_wrap produces. Feeding it raw frames meant it emitted
		 * nothing at all for a whole campaign, and the run said so rather than passing -
		 * which is the only reason this line is right now. */
		uint8_t wire[sizeof(buf) + 8];
		size_t wire_len = cr_wrap(wire, sizeof(wire), buf, len);

		if (wire_len == 0) {
			continue;
		}
		if (rnd() & 1) {
			/* Sometimes split it, because a link delivers when it likes and the
			 * deframer's state across chunks is the part worth breaking. */
			size_t cut = rnd_below(wire_len);

			cr_deframer_feed(&deframer, wire, cut, on_frame, NULL);
			cr_deframer_feed(&deframer, wire + cut, wire_len - cut, on_frame, NULL);
		} else {
			cr_deframer_feed(&deframer, wire, wire_len, on_frame, NULL);
		}
	}

	printf("  TC: %lu accepted, %lu rejected\n", accepted_tc, rejected_tc);
	printf("  TM: %lu accepted, %lu rejected\n", accepted_tm, rejected_tm);
	printf("  deframer emitted %lu frames\n", frames_deframed);

	/* A campaign that never accepted anything walked past the decoder without entering it, and a
	 * campaign that never rejected anything was not given malformed input. Either way the run
	 * proves nothing, and saying so is the difference between a fuzzer and a green line. */
	if (accepted_tc == 0 || rejected_tc == 0) {
		printf("FAIL the campaign only produced one outcome for TC; it explored nothing\n");
		failures++;
	}
	if (accepted_tm == 0 || rejected_tm == 0) {
		printf("FAIL the campaign only produced one outcome for TM; it explored nothing\n");
		failures++;
	}
	if (frames_deframed == 0) {
		printf("FAIL the deframer never emitted a frame; it was not exercised\n");
		failures++;
	}

	if (failures) {
		printf("%d FAILURES\n", failures);
		return 1;
	}
	printf("no crashes, no invariant violations\n");
	return 0;
}
