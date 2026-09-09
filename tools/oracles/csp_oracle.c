/*
 * The CSP oracle: libcsp itself, printing what it puts on the wire.
 *
 * tests/golden/csp.json is this program's output, and until now the program was not in the
 * repository. `tools/gen_golden.py` called `./csp_oracle`, nothing said where that came from, and
 * the handoff document had already noticed - so the golden vectors were unreproducible, which for
 * an INDEPENDENT ORACLE is most of the point gone. This is that program, reconstructed to match the
 * committed output byte for byte; `tools/oracles/build.sh` builds it and gen_golden.py compares.
 *
 * The whole idea is that these vectors come from libcsp and not from CubeRange. Two implementations
 * of ours agreeing proves nothing, because both can hold the same misreading - and one nearly did:
 * libcsp picks its header layout from csp_conf.version at RUNTIME, defaulting to 2, so "we use CSP
 * v1" was true in the design and false in the firmware for a while. Asking the library is the only
 * way to be sure which layout is real.
 *
 * SPDX-License-Identifier: Apache-2.0   (libcsp itself is MIT and is not vendored here)
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include <csp/csp.h>
#include <csp/csp_id.h>
#include <csp/csp_buffer.h>
#include <csp/interfaces/csp_if_can.h>

/* ------------------------------------------------------------------ capture */

#define MAX_FRAMES 32

static struct {
	uint32_t id;
	uint8_t data[8];
	uint8_t dlc;
} captured[MAX_FRAMES];
static int n_captured;

static int capture_tx(void *driver_data, uint32_t id, const uint8_t *data, uint8_t dlc)
{
	(void)driver_data;
	if (n_captured < MAX_FRAMES) {
		captured[n_captured].id = id;
		captured[n_captured].dlc = dlc;
		memcpy(captured[n_captured].data, data, dlc > 8 ? 8 : dlc);
		n_captured++;
	}
	return CSP_ERR_NONE;
}

static void hex(const uint8_t *b, int n)
{
	for (int i = 0; i < n; i++)
		printf("%02X", b[i]);
}

/* CFP v1 identifier layout, from libcsp/include/csp/interfaces/csp_if_can.h:77-119.
 * Decoded here as well as printed so the file records the field split it was read with. */
static void decode_cfp(uint32_t id, unsigned *src, unsigned *dst, unsigned *type,
                       unsigned *remain, unsigned *cfp_id)
{
	*src    = (id >> 24) & 0x1F;
	*dst    = (id >> 19) & 0x1F;
	*type   = (id >> 18) & 0x01;
	*remain = (id >> 10) & 0xFF;
	*cfp_id = (id >> 0)  & 0x3FF;
}

/* ------------------------------------------------------------------ header vectors */

struct hdr_case {
	uint8_t pri, flags;
	uint16_t src, dst;
	uint8_t dport, sport;
};

static const struct hdr_case HDRS[] = {
	{0, 0x00,  0,  0,  0,  0},
	{3, 0xFF, 31, 31, 63, 63},
	{2, 0x00,  1,  2, 10, 20},
	{1, 0x1B,  5, 10,  8, 63},
	{0, 0x00, 31,  0,  0,  0},
	{0, 0x00,  0, 31,  0,  0},
	{0, 0x00,  0,  0, 63,  0},
	{0, 0x00,  0,  0,  0, 63},
	{3, 0x00,  0,  0,  0,  0},
};

static void header_vectors(void)
{
	printf("\n--- A. CSPv1 32-bit header golden vectors (csp_id_prepend) ---\n");
	for (unsigned i = 0; i < sizeof(HDRS) / sizeof(HDRS[0]); i++) {
		const struct hdr_case *c = &HDRS[i];
		csp_packet_t *p = csp_buffer_get(0);

		if (p == NULL) {
			printf("csp_buffer_get failed\n");
			return;
		}
		p->id.pri = c->pri;
		p->id.flags = c->flags;
		p->id.src = c->src;
		p->id.dst = c->dst;
		p->id.dport = c->dport;
		p->id.sport = c->sport;
		p->length = 0;
		csp_id_prepend(p);

		printf("CSPv1 hdr pri=%u src=%u dst=%u dport=%u sport=%u flags=0x%02X -> ",
		       c->pri, c->src, c->dst, c->dport, c->sport, c->flags);
		hex(p->frame_begin, 4);

		/* Round-trip through the library's own parser, so the vector records a layout
		 * libcsp both writes and reads rather than one we merely observed it write. */
		p->frame_length = 4;
		csp_id_strip(p);
		printf("   (extract-back: pri=%u src=%u dst=%u dport=%u sport=%u flags=0x%02X %s)\n",
		       p->id.pri, p->id.src, p->id.dst, p->id.dport, p->id.sport, p->id.flags,
		       (p->id.pri == c->pri && p->id.src == c->src && p->id.dst == c->dst &&
		        p->id.dport == c->dport && p->id.sport == c->sport && p->id.flags == c->flags)
		           ? "OK" : "MISMATCH");
		csp_buffer_free(p);
	}
}

/* ------------------------------------------------------------------ fragmentation vectors */

struct frag_case {
	const char *label;
	uint16_t length;
	uint16_t src, dst;
	uint8_t dport, sport;
};

static const struct frag_case FRAGS[] = {
	{"B1 len=0  (fits in BEGIN)",     0, 1, 2, 10, 20},
	{"B2 len=2  (fits in BEGIN)",     2, 1, 2, 10, 20},
	{"B3 len=3  (BEGIN + 1 MORE)",    3, 1, 2, 10, 20},
	{"B4 len=10 (BEGIN + 1 MORE)",   10, 1, 2, 10, 20},
	{"B5 len=11 (BEGIN + 2 MORE)",   11, 1, 2, 10, 20},
	{"B6 len=18 (BEGIN + 2 MORE)",   18, 5, 10, 8, 63},
	/* Nine frames: the case that shows REMAIN counting down and the last frame short. A
	 * fragmentation bug that only appears past the first MORE frame hides in the shorter cases. */
	{"B7 len=64",                    64, 3,  4, 1,  2},
};

static void fragmentation_vectors(csp_iface_t *iface)
{
	printf("\n--- B. CFP1-over-CAN fragmentation golden vectors ---\n");
	for (unsigned i = 0; i < sizeof(FRAGS) / sizeof(FRAGS[0]); i++) {
		const struct frag_case *c = &FRAGS[i];
		csp_packet_t *p = csp_buffer_get(c->length);

		if (p == NULL) {
			printf("csp_buffer_get failed\n");
			return;
		}
		/* A recognisable ramp, so a byte that moved between frames is visible in the hex. */
		for (uint16_t b = 0; b < c->length; b++)
			p->data[b] = (uint8_t)(0xA0 + b);
		p->length = c->length;
		p->id.pri = 2;
		p->id.flags = 0;
		p->id.src = c->src;
		p->id.dst = c->dst;
		p->id.dport = c->dport;
		p->id.sport = c->sport;

		n_captured = 0;
		printf("%s: CSP payload len=%u src=%u dst=%u dport=%u sport=%u\n",
		       c->label, c->length, c->src, c->dst, c->dport, c->sport);
		iface->nexthop(iface, c->dst, p, 1);

		for (int f = 0; f < n_captured; f++) {
			unsigned src, dst, type, remain, cfp_id;

			printf("  FRAME %d: can_id=0x%08X (29b) dlc=%u data=",
			       f, captured[f].id, captured[f].dlc);
			hex(captured[f].data, captured[f].dlc);
			printf("\n");
			decode_cfp(captured[f].id, &src, &dst, &type, &remain, &cfp_id);
			printf("    decoded: SRC=%u DST=%u TYPE=%u REMAIN=%u ID=%u\n",
			       src, dst, type, remain, cfp_id);
		}
	}
}

int main(void)
{
	static csp_iface_t iface;
	static csp_can_interface_data_t ifdata;

	/* v1 EXPLICITLY. libcsp defaults csp_conf.version to 2 and chooses the header and CFP
	 * layouts from it at runtime, so an oracle that omitted this line would faithfully record
	 * the wrong protocol - which is exactly the defect this project hit in the firmware. */
	csp_conf.version = 1;
	csp_init();

	printf("=== libcsp version config: CSP v%u, header size %u bytes ===\n",
	       csp_conf.version, (unsigned)csp_id_get_header_size());
	printf("=== max nodeid=%u max port=%u host bits=%u ===\n",
	       csp_id_get_max_nodeid(), csp_id_get_max_port(), 5);

	header_vectors();

	ifdata.tx_func = capture_tx;
	ifdata.cfp_packet_counter = 0;
	ifdata.pbufs = NULL;
	iface.name = "ORACLE";
	iface.interface_data = &ifdata;
	iface.driver_data = NULL;
	iface.addr = 1;
	iface.netmask = 5;
	if (csp_can_add_interface(&iface) != CSP_ERR_NONE) {
		printf("csp_can_add_interface failed\n");
		return 1;
	}

	fragmentation_vectors(&iface);
	return 0;
}
