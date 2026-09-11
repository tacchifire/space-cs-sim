/* Native (host) unit test for the firmware codec. Built with plain gcc - no Zephyr, no board -
 * so it runs in CI in milliseconds and can be fuzzed later.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>

#include "cuberange_proto.h"

static int failures;

#define CHECK(cond, msg) do { \
	if (!(cond)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, msg); failures++; } \
} while (0)

static void test_crc_check_vector(void)
{
	CHECK(cr_crc16((const uint8_t *)"123456789", 9) == 0x29B1,
	      "CRC-16/IBM-3740 catalogue check value");
	CHECK(cr_crc16((const uint8_t *)"", 0) == 0xFFFF, "empty input yields the init value");
}

static void test_tc_frame_round_trip(void)
{
	const uint8_t payload[] = {0x01, 0x02, 0x03, 0x04};
	uint8_t frame[64];
	int n = cr_encode_tc_frame(frame, sizeof(frame), payload, sizeof(payload), 7);

	CHECK(n > 0, "encode returned a length");
	CHECK(cr_crc16(frame, (size_t)n) == 0x0000, "FECF residue is zero");

	uint8_t seq;
	const uint8_t *out;
	size_t out_len;

	CHECK(cr_decode_tc_frame(frame, (size_t)n, &seq, &out, &out_len) == 0, "decode succeeded");
	CHECK(seq == 7, "sequence number survived");
	CHECK(out_len == sizeof(payload), "payload length survived");
	CHECK(memcmp(out, payload, out_len) == 0, "payload bytes survived");
}

static void test_tm_frame_round_trip(void)
{
	const uint8_t payload[] = {0x10, 0x11, 0x12};
	uint8_t frame[64];
	int n = cr_encode_tm_frame(frame, sizeof(frame), payload, sizeof(payload), 3, 4);

	CHECK(n > 0, "TM encode returned a length");
	CHECK(cr_crc16(frame, (size_t)n) == 0x0000, "TM FECF residue is zero");

	uint8_t mc, vc;
	const uint8_t *out;
	size_t out_len;

	CHECK(cr_decode_tm_frame(frame, (size_t)n, &mc, &vc, &out, &out_len) == 0, "TM decode ok");
	CHECK(mc == 3 && vc == 4, "TM frame counts survived");
	CHECK(out_len == sizeof(payload) && memcmp(out, payload, out_len) == 0, "TM payload survived");
}

static void test_decode_rejects_corruption(void)
{
	const uint8_t payload[] = {0xAA, 0xBB};
	uint8_t frame[64];
	int n = cr_encode_tc_frame(frame, sizeof(frame), payload, sizeof(payload), 1);

	frame[6] ^= 0xFF;
	uint8_t seq;
	const uint8_t *out;
	size_t out_len;

	CHECK(cr_decode_tc_frame(frame, (size_t)n, &seq, &out, &out_len) != 0,
	      "corrupted frame is rejected");
}

struct collect {
	int count;
	uint8_t last[256];
	size_t last_len;
};

static void on_frame(const uint8_t *frame, size_t len, void *ctx)
{
	struct collect *c = ctx;

	c->count++;
	c->last_len = len < sizeof(c->last) ? len : sizeof(c->last);
	memcpy(c->last, frame, c->last_len);
}

static void test_deframer_across_chunk_boundaries(void)
{
	const uint8_t payload[] = {0x55, 0x66, 0x77};
	uint8_t frame[64], stream[128];
	int n = cr_encode_tc_frame(frame, sizeof(frame), payload, sizeof(payload), 2);
	size_t total = cr_wrap(stream, sizeof(stream), frame, (size_t)n);

	CHECK(total > 0, "wrap produced a stream");

	for (size_t chunk = 1; chunk <= total; chunk++) {
		cr_deframer_t d;
		struct collect c = {0};

		cr_deframer_init(&d);
		for (size_t i = 0; i < total; i += chunk) {
			size_t take = (i + chunk <= total) ? chunk : total - i;

			cr_deframer_feed(&d, stream + i, take, on_frame, &c);
		}
		CHECK(c.count == 1, "exactly one frame emerged");
		CHECK(c.last_len == (size_t)n, "frame length matched");
		CHECK(memcmp(c.last, frame, c.last_len) == 0, "frame bytes matched");
	}
}

static void test_deframer_resynchronises_after_garbage(void)
{
	const uint8_t payload[] = {0x99};
	uint8_t frame[64], stream[128];
	int n = cr_encode_tc_frame(frame, sizeof(frame), payload, sizeof(payload), 5);
	const char *junk = "garbage-before-the-marker";
	size_t junk_len = strlen(junk);
	size_t wrapped = cr_wrap(stream, sizeof(stream), frame, (size_t)n);

	cr_deframer_t d;
	struct collect c = {0};

	cr_deframer_init(&d);
	cr_deframer_feed(&d, (const uint8_t *)junk, junk_len, on_frame, &c);
	cr_deframer_feed(&d, stream, wrapped, on_frame, &c);
	CHECK(c.count == 1, "resynchronised after leading garbage");
	CHECK(c.last_len == (size_t)n && memcmp(c.last, frame, c.last_len) == 0,
	      "the frame after the garbage was intact");
}


/* The virtual channel field, which COMM needs because CCSDS keeps the frame sequence number per
 * virtual channel and this implementation kept one counter for the whole link. Compared against
 * hand-built headers rather than against our own encoder: cr_encode_tc_frame only ever emits
 * CR_VCID, so a round trip through it would agree with itself about a field it never varies. */
static void test_tc_frame_vcid_is_read_from_octet_two(void)
{
	/* CCSDS 232.0-B-4 4.1.2.5: six bits, octet 2 bits 7..2. Built here, not encoded. */
	static const struct { uint8_t octet2; uint8_t vcid; } cases[] = {
		{ 0x00, 0 }, { 0x04, 1 }, { 0xFC, 63 }, { 0x80, 32 }, { 0x7C, 31 },
	};
	for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
		uint8_t frame[8] = { 0x00, 0xA9, cases[i].octet2, 0x07, 0x00, 0x00, 0x00, 0x00 };
		CHECK(cr_tc_frame_vcid(frame, sizeof(frame)) == cases[i].vcid,
		      "VCID read from octet 2 bits 7..2");
	}
	/* The length field shares octet 2's low two bits and must not leak into the VCID. */
	uint8_t shared[8] = { 0x00, 0xA9, 0x03, 0xFF, 0, 0, 0, 0 };
	CHECK(cr_tc_frame_vcid(shared, sizeof(shared)) == 0, "the frame length bits are not VCID");

	uint8_t stub[3] = { 0, 0, 0 };
	CHECK(cr_tc_frame_vcid(stub, sizeof(stub)) == 0xFF,
	      "a frame too short to hold the field reports a value no VCID can take");
}

int main(void)
{
	test_crc_check_vector();
	test_tc_frame_round_trip();
	test_tm_frame_round_trip();
	test_decode_rejects_corruption();
	test_deframer_across_chunk_boundaries();
	test_deframer_resynchronises_after_garbage();
	test_tc_frame_vcid_is_read_from_octet_two();

	if (failures) {
		printf("%d FAILURES\n", failures);
		return 1;
	}
	printf("all native codec tests passed\n");
	return 0;
}
