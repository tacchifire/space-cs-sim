/*
 * CubeRange COMM node: the gateway between the space link and the internal bus.
 *
 * uplink:   usart2 bytes -> deframe -> TC frame -> Space Packet -> CSP to the OBC
 * downlink: CSP from the OBC -> TM frame -> lab framing -> usart2 bytes
 *
 * COMM deliberately does not parse PUS. It is a link-layer device; service dispatch belongs to
 * the OBC. That boundary is what makes an attacker who owns COMM different from one who owns the
 * OBC, which several planned exercises depend on.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/uart.h>
#include <zephyr/sys/printk.h>
#include <string.h>

#include <csp/csp.h>
#include <csp/drivers/can_zephyr.h>

#include "cuberange_proto.h"

#ifndef COMM_ADDR
#define COMM_ADDR 5
#endif
#ifndef OBC_ADDR
#define OBC_ADDR 1
#endif
/* CSP port for the PUS pipe. NOT 17: libcsp's CSP_PORT_MAX_BIND defaults to 16, csp_port.c
 * rejects any lookup above it, and ports above 16 are reserved for outgoing ephemeral source
 * ports (conn->sport_outgoing = CSP_PORT_MAX_BIND + 1 + i). Binding 17 to match the PUS service
 * number silently dropped every uplink packet while csp_ping on port 1 kept working - a mnemonic
 * is not worth resizing the library's port table. */
#define CSP_PORT_PUS  10
/* Link statistics, COMM -> OBC. A port of its own rather than a PUS service on port 10: what
 * crosses here is not a telecommand and must not reach handle_space_packet, which counts what it
 * is given. A radio reporting its own refusals into the telecommand counter would be a detector
 * whose readings are its own. */
#define CSP_PORT_LINKSTATS 12
#define CAN_BITRATE   1000000

/* Anti-replay on the space link. The TC transfer frame carries an 8-bit sequence number; the
 * mitigated build accepts a frame only if its sequence is strictly ahead of the last accepted one,
 * compared as a signed difference so the counter can wrap. The vulnerable build accepts anything,
 * which is what makes a captured frame reusable forever.
 *
 * This is deliberately the weakest useful control: it stops a straight replay and nothing else. It
 * does not authenticate the sender, and an attacker who can suppress the real uplink can still run
 * ahead of the counter. EX-L01's mitigation notes say so. */
#ifndef CUBERANGE_COMM_ANTIREPLAY
#define CUBERANGE_COMM_ANTIREPLAY 0
#endif
#ifndef CUBERANGE_COMM_ANTIREPLAY_PER_VC
#define CUBERANGE_COMM_ANTIREPLAY_PER_VC 0
#endif
#ifndef CUBERANGE_COMM_CROSSLINK
#define CUBERANGE_COMM_CROSSLINK 0
#endif
#ifndef CUBERANGE_COMM_SDLS
#define CUBERANGE_COMM_SDLS 0
#endif

/* EX-U03's only difference: whether the radio ever tells anyone what it refused.
 *
 * Every link-layer control this range has - the FECF check, the anti-replay counter, the SDLS MAC
 * - ends in a printk on a console nobody off the spacecraft can read. They WORK. A forged frame
 * is refused, a replay is refused, a truncated frame is refused, and the attack fails completely
 * and leaves no record anywhere a human will ever look.
 *
 * That is EX-G04's finding - a control that cannot report is a control the ground cannot use -
 * arriving at the layer below everything EX-G04 was about. And it is the opposite blind spot from
 * EX-U02's: that counter is on the OBC, BEHIND this check, so it sees only the attacker who has
 * the key. This one sees the attacker who does not, which is the more common attacker by a wide
 * margin.
 *
 * The counts go to the OBC over CSP and ride the housekeeping beacon, rather than COMM growing a
 * telemetry path of its own. A radio that originates its own APID is what a real mission would
 * do; the OBC aggregating subsystem housekeeping is also what a real mission would do, and it is
 * the one that needs no new plumbing. The limit is written down in EX-U03's mitigation. */
#ifndef CUBERANGE_COMM_LINK_STATS
#define CUBERANGE_COMM_LINK_STATS 0
#endif
#ifndef CUBERANGE_COMM_LINK_STATS_MS
#define CUBERANGE_COMM_LINK_STATS_MS 2000
#endif

/* Included after the default above, not beside the other headers. A `#if` on a macro that has not
 * been defaulted yet is 0 whatever the build says, and the symptom would be a build with SDLS on
 * whose verifier was compiled out. */
#if CUBERANGE_COMM_SDLS
#include "cuberange_sdls.h"
#include "cuberange_keys.h"
#include <mbedtls/gcm.h>
#endif

#define RX_RING_SIZE  512
#define ROUTER_STACK  1024
/* link_task keeps a cr_deframer_t on its stack - 1032 octets of frame buffer - and the SDLS build
 * calls sdls_verify from inside on_tc_frame, whose frame carries a 424-octet mbedtls_gcm_context.
 * 2048 held the first and not both. */
#if CUBERANGE_COMM_SDLS
#define LINK_STACK    3072
#else
#define LINK_STACK    2048
#endif
#define DOWN_STACK    2048

static const struct device *link_dev;
static csp_iface_t *can_iface;
#if CUBERANGE_COMM_CROSSLINK
static csp_iface_t *xlink_iface;
#endif

K_MSGQ_DEFINE(link_rx_q, 1, RX_RING_SIZE, 1);

static void link_isr(const struct device *dev, void *user_data)
{
	ARG_UNUSED(user_data);

	if (!uart_irq_update(dev)) {
		return;
	}
	while (uart_irq_rx_ready(dev)) {
		uint8_t byte;

		if (uart_fifo_read(dev, &byte, 1) != 1) {
			break;
		}
		/* Dropping under overrun is correct here: the ground station will retransmit, and
		 * blocking in an ISR would stall the whole node. */
		(void)k_msgq_put(&link_rx_q, &byte, K_NO_WAIT);
	}
}

static void link_write(const uint8_t *buf, size_t len)
{
	for (size_t i = 0; i < len; i++) {
		uart_poll_out(link_dev, buf[i]);
	}
}

#if CUBERANGE_COMM_SDLS
/* Verify one authenticated TC frame, and answer only whether it verified.
 *
 * The order is the order a spacecraft must use and is not a preference: shape, then the declared
 * length, then the FECF, then the MAC. cr_sdls_split does the first three and touches no key;
 * running a cipher first would mean running it over octet counts an attacker chose.
 *
 * mbedtls_gcm_auth_decrypt with a zero-length input is what authentication-only means: the
 * payload is additional authenticated data, there is no ciphertext, and the MAC is the GCM tag.
 */
/* THE SECURITY ASSOCIATION TABLE, and it is the oldest thing this range had named and not built.
 *
 * Until EX-S03 this function verified against one SPI and one key, and the comment where the SPI
 * was checked said so: "one association is all this range has". CCSDS 355.0-B-2 puts a key, a
 * cipher mode, an anti-replay sequence number and a STATE in an SA, and a mission has several
 * because that is how a key is retired: activate the new SA, deactivate the old one, and a frame
 * on the old SPI stops opening the door. Both halves are controls, and EX-S03 is about a rotation
 * that did only the first one.
 *
 * `operational` is the state field, reduced to the one bit this range needs. CryptoLib carries
 * four states (unkeyed, keyed, operational, expired) and the distinction matters for key
 * management this range does not do; what matters here is that a frame arriving for a
 * deactivated SA is refused for a REASON, and the reason is reported.
 *
 * The table is const and compiled in. An SA that can be activated or deactivated by telecommand
 * is what a real mission has, and a telecommand that can deactivate the operator's own SA is a
 * denial-of-service with a valid MAC on it - that is named in EX-S03's mitigation as the next
 * thing, not implemented here.
 */
#ifndef CUBERANGE_COMM_SA_DEACTIVATION
#define CUBERANGE_COMM_SA_DEACTIVATION 0
#endif

struct cr_sdls_sa {
	uint16_t spi;
	const uint8_t *key;
	bool operational;
};

static const struct cr_sdls_sa sa_table[] = {
	/* The association every exercise before EX-S03 uses. Deactivated in exactly one build in
	 * this repository - EX-S03's mitigated half - which is what makes that half a rotation
	 * rather than an addition. */
	{ CR_SDLS_SPI, cr_sdls_key, CUBERANGE_COMM_SA_DEACTIVATION ? false : true },
	{ CR_SDLS_SPI_ROTATED, cr_sdls_key_rotated, true },
};

#define SA_COUNT ((int)ARRAY_SIZE(sa_table))

/* Which SA the last refused frame claimed, and why it was refused. Reported to the OBC so it can
 * reach the ground: "somebody is using the key you retired" and "somebody is guessing" are
 * different messages, and a bare refusal count cannot carry either. */
#if CUBERANGE_COMM_LINK_STATS
static uint16_t link_refused_spi;
#endif

/* Returns the index of the SA that verified the frame, or a negative reason. The reason is
 * negative and distinct per cause on purpose: the caller logs it, and "no such SA", "that SA is
 * deactivated" and "the MAC does not verify" send an operator to three different places. */
#define SA_ERR_MALFORMED   (-1)
#define SA_ERR_NO_SUCH_SA  (-2)
#define SA_ERR_DEACTIVATED (-3)
#define SA_ERR_BAD_MAC     (-4)

static int sdls_verify_sa(const uint8_t *frame, size_t len, struct cr_sdls_parts *parts)
{
	if (cr_sdls_split(frame, len, CR_SDLS_IV_LEN, CR_SDLS_SN_LEN, CR_SDLS_MAC_LEN, parts) != 0) {
		return SA_ERR_MALFORMED;
	}

	int found = -1;

	for (int i = 0; i < SA_COUNT; i++) {
		if (sa_table[i].spi == parts->spi) {
			found = i;
			break;
		}
	}
	if (found < 0) {
		return SA_ERR_NO_SUCH_SA;
	}
	/* BEFORE the MAC, not after. A deactivated SA's key must not be used to verify anything -
	 * checking the MAC first and the state second would mean the retired key still decides
	 * whether a frame is well-formed, and an attacker holding it could tell a deactivated SA
	 * from a nonexistent one by the timing. */
	if (!sa_table[found].operational) {
		return SA_ERR_DEACTIVATED;
	}

	mbedtls_gcm_context gcm;
	mbedtls_gcm_init(&gcm);
	bool ok = false;

	if (mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, sa_table[found].key, 256) == 0) {
		ok = mbedtls_gcm_auth_decrypt(&gcm, 0, parts->iv, parts->iv_len,
					      parts->aad, parts->aad_len,
					      parts->mac, parts->mac_len, NULL, NULL) == 0;
	}
	mbedtls_gcm_free(&gcm);
	return ok ? found : SA_ERR_BAD_MAC;
}

/* One published NIST AES-256-GCM vector, at boot, before anything depends on the answer.
 *
 * Renode models registers rather than physics, and mbedTLS here is software - but "the crypto
 * library built" and "the crypto library computes the right tag on this target" are different
 * claims, and only one of them is checked by the build succeeding. A wrong answer would show up
 * as every telecommand being refused, which is indistinguishable from a wrong key, a wrong
 * layout, or a link that dropped the frame. That is EX-G04's problem again, so it gets an answer
 * that is printed once and is either right or loudly not.
 *
 * Key and IV all zero, no AAD, empty plaintext; tag 530f8afb... The same vector is in
 * tests/golden/sdls.json, checked there against OpenSSL.
 */
static void sdls_selftest(void)
{
	static const uint8_t zero_key[32] = {0};
	static const uint8_t zero_iv[12] = {0};
	static const uint8_t expect[16] = {
		0x53, 0x0F, 0x8A, 0xFB, 0xC7, 0x45, 0x36, 0xB9,
		0xA9, 0x63, 0xB4, 0xF1, 0xC4, 0xCB, 0x73, 0x8B,
	};
	uint8_t tag[16] = {0};
	mbedtls_gcm_context gcm;

	mbedtls_gcm_init(&gcm);
	int rc = mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, zero_key, 256);

	if (rc == 0) {
		rc = mbedtls_gcm_crypt_and_tag(&gcm, MBEDTLS_GCM_ENCRYPT, 0, zero_iv,
					       sizeof(zero_iv), NULL, 0, NULL, NULL,
					       sizeof(tag), tag);
	}
	mbedtls_gcm_free(&gcm);

	if (rc != 0) {
		printk("COMM: SDLS SELFTEST FAILED - mbedTLS returned %d\n", rc);
		return;
	}
	if (memcmp(tag, expect, sizeof(expect)) != 0) {
		printk("COMM: SDLS SELFTEST FAILED - wrong tag\n");
		return;
	}
	printk("COMM: SDLS self-test passed (NIST AES-256-GCM vector)\n");
}
#endif /* CUBERANGE_COMM_SDLS */

/* One deframed TC frame: strip the frame header and hand the Space Packet to the OBC. */
#if CUBERANGE_COMM_LINK_STATS
/* Sixteen bits each, read by the ground as differences for the reason EX-U02's are: an absolute
 * read of a wrapping counter is wrong once every 65536. `refused` counts frames this radio threw
 * away; `rx` counts frames that arrived at all. The PAIR is the signal - twenty refused out of
 * twenty-four is a sentence, twenty refused is not - because a ratio survives a link whose frame
 * rate nobody on the ground knows.
 *
 * It does NOT separate an adversary from bad weather, and it would be easy to write here that it
 * does. A bad FECF is noise and a valid FECF with a bad MAC is somebody, but this counter is one
 * number for both: in the SDLS build the FECF check lives inside sdls_verify, so the refusal
 * reason is gone by the time anything counts it. Carrying the reason is what would make that
 * distinction, and it is written down in EX-U03's mitigation as missing rather than implied
 * here as present. */
static uint16_t link_frames_rx;
static uint16_t link_frames_refused;

static void send_link_stats(void)
{
	csp_packet_t *out = csp_buffer_get(6);

	if (out == NULL) {
		return;
	}
	out->data[0] = (uint8_t)(link_frames_rx >> 8);
	out->data[1] = (uint8_t)link_frames_rx;
	out->data[2] = (uint8_t)(link_frames_refused >> 8);
	out->data[3] = (uint8_t)link_frames_refused;
	/* Which association the last refused frame claimed. One value and not a histogram, which
	 * EX-S03's mitigation names as the limit: a burst of refusals on two different SPIs reports
	 * only the second. It is enough to tell "the key you retired" from "no key at all", which is
	 * the question a rotation raises. */
	out->data[4] = (uint8_t)(link_refused_spi >> 8);
	out->data[5] = (uint8_t)link_refused_spi;
	out->length = 6;

	csp_conn_t *conn = csp_connect(CSP_PRIO_NORM, OBC_ADDR, CSP_PORT_LINKSTATS, 1000,
				       CSP_O_NONE);

	if (conn == NULL) {
		csp_buffer_free(out);
		return;
	}
	csp_send(conn, out);
	csp_close(conn);
}

static void stats_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	while (1) {
		k_sleep(K_MSEC(CUBERANGE_COMM_LINK_STATS_MS));
		send_link_stats();
	}
}

/* Unconditional, not "when something changed". A report that only appears when there is
 * something to report makes its own absence ambiguous, and this range has an exercise about
 * exactly that: EX-L03's beacon exists because silence has to mean something. */
#define STATS_STACK 2048
K_THREAD_DEFINE(stats_id, STATS_STACK, stats_task, NULL, NULL, NULL, 2, 0, K_TICKS_FOREVER);
#endif /* CUBERANGE_COMM_LINK_STATS */

static void on_tc_frame(const uint8_t *frame, size_t len, void *ctx)
{
	ARG_UNUSED(ctx);

#if CUBERANGE_COMM_LINK_STATS
	link_frames_rx++;
#endif
	uint8_t seq;
	const uint8_t *packet;
	size_t packet_len;

#if CUBERANGE_COMM_SDLS
	/* FIRST, and instead of cr_decode_tc_frame - not after it.
	 *
	 * An authenticated frame is not a plain one with something appended: its payload starts
	 * after the security header, so cr_decode_tc_frame would hand the OBC the SPI and the IV as
	 * if they were the first octets of a Space Packet. Authenticated and plain framing are two
	 * formats and this build speaks one of them.
	 *
	 * Nothing below this point runs on an unverified frame. The anti-replay counter in
	 * particular: EX-L01's counter reads the frame sequence number out of the primary header,
	 * which an attacker writes, and SDLS carries its own sequence number INSIDE the
	 * authenticated portion. Checking the outer one first would be checking the attacker's copy.
	 */
	struct cr_sdls_parts parts;

	int sa = sdls_verify_sa(frame, len, &parts);

	if (sa < 0) {
		/* Named reasons, because a refusal an operator cannot read is the thing EX-G04 and
		 * EX-U03 are both about. "The SA you retired" and "a MAC that does not verify" are
		 * different incidents; one is a rotation that did not finish and the other is
		 * somebody without the key. */
		static const char *const why[] = {
			[-SA_ERR_MALFORMED] = "not a well-formed authenticated frame",
			[-SA_ERR_NO_SUCH_SA] = "no such security association",
			[-SA_ERR_DEACTIVATED] = "that security association is DEACTIVATED",
			[-SA_ERR_BAD_MAC] = "the MAC does not verify",
		};

		/* The SPI is printed only when the frame HAD one. A plain frame's security
		 * header is whatever octets were in those positions, and "SPI 24" about a frame
		 * with no security header sends an operator to look up an association nobody
		 * used. Measured: an unauthenticated frame reported SPI 24. */
		if (sa == SA_ERR_MALFORMED) {
			printk("COMM: REFUSED a TC frame - %s\n", why[-sa]);
		} else {
			printk("COMM: REFUSED a TC frame, SPI %u - %s\n",
			       (unsigned int)parts.spi, why[-sa]);
		}
#if CUBERANGE_COMM_LINK_STATS
		link_frames_refused++;
		/* Only when the frame said which SA it was for. A malformed frame's SPI field is
		 * whatever octets happened to be there, and reporting it would have the ground
		 * investigating an association nobody used. */
		if (sa != SA_ERR_MALFORMED) {
			link_refused_spi = parts.spi;
		}
#endif
		return;
	}
	/* Anti-replay on the AUTHENTICATED sequence number, and this is the part that makes SDLS
	 * worth having over EX-L01's counter.
	 *
	 * EX-L01 reads the frame sequence number out of the TC primary header - a field the attacker
	 * writes, outside anything signed - so its own mitigation notes say a recording can be
	 * replayed with the counter advanced. The SDLS sequence number is INSIDE the authenticated
	 * portion: changing it invalidates the MAC, and the MAC cannot be recomputed without the key.
	 *
	 * ONE COUNTER PER SECURITY ASSOCIATION, which is where CCSDS 355.0-B-2 puts anti-replay
	 * state, and it is what this comment used to say was not implemented. EX-U02 measured what
	 * the single-counter version cost: an intruder holding the operator's key advanced it, the
	 * operator's next frames arrived behind it, and COMM refused the legitimate station at the
	 * link layer with no acceptance and no refusal reaching the ground.
	 *
	 * Two SAs now get two counters. Note what that does NOT fix: two transmitters sharing ONE
	 * SA still collide, because they share its counter - which is correct, and is exactly EX-U02
	 * whose intruder stole the key to the SA the operator was using. The fix is per-SA, and a
	 * stolen key does not give the thief a second SA.
	 *
	 * Strictly greater, not "not equal". A window would accept out-of-order frames within it, and
	 * CCSDS 355.0-B-2 provides for one (the SA's arsnw); a single high-water mark is the
	 * degenerate window of size one and is what this range needs to make the lesson visible.
	 */
	static uint32_t sdls_sn_seen[SA_COUNT];
	static bool sdls_sn_have[SA_COUNT];

	if (sdls_sn_have[sa] && parts.seq_num <= sdls_sn_seen[sa]) {
		printk("COMM: REPLAY - SPI %u frame with sequence %u, already seen %u\n",
		       (unsigned int)parts.spi, (unsigned int)parts.seq_num,
		       (unsigned int)sdls_sn_seen[sa]);
#if CUBERANGE_COMM_LINK_STATS
		link_frames_refused++;
		link_refused_spi = parts.spi;
#endif
		return;
	}
	sdls_sn_seen[sa] = parts.seq_num;
	sdls_sn_have[sa] = true;

	seq = frame[4];
	packet = parts.payload;
	packet_len = parts.payload_len;
	printk("COMM: authenticated frame, SPI %u seq %u, %u octets of payload\n",
	       (unsigned int)parts.spi, (unsigned int)parts.seq_num,
	       (unsigned int)parts.payload_len);
#else
	if (cr_decode_tc_frame(frame, len, &seq, &packet, &packet_len) != 0) {
		printk("COMM: dropping a TC frame that failed its FECF or length check\n");
#if CUBERANGE_COMM_LINK_STATS
		link_frames_refused++;
#endif
		return;
	}
#endif
#if CUBERANGE_COMM_ANTIREPLAY
#if CUBERANGE_COMM_ANTIREPLAY_PER_VC
	/* One counter PER VIRTUAL CHANNEL, which is what CCSDS 232.0-B-4 specifies: COP-1's FARM
	 * state is per VC, not per link. The single-counter version below is correct exactly as
	 * long as there is one ground station, and locks the second one out completely the moment
	 * there are two - its frames carry sequence numbers behind the first station's and are
	 * logged as REPLAYS, so the operator goes looking for an attacker who is their colleague.
	 *
	 * Sixty-four entries because the VCID field is six bits. A fixed table and no eviction: a
	 * cache with a policy is a policy an attacker can drive. 128 bytes.
	 */
	static uint8_t last_seq_vc[64];
	static uint8_t have_vc[64];
	uint8_t vc = cr_tc_frame_vcid(frame, len);

	if (vc > 63) {
		printk("COMM: REJECTED frame with no readable virtual channel\n");
#if CUBERANGE_COMM_LINK_STATS
		link_frames_refused++;
#endif
		return;
	}
	if (have_vc[vc]) {
		int8_t ahead = (int8_t)(seq - last_seq_vc[vc]);

		if (ahead <= 0) {
			printk("COMM: REJECTED replayed frame seq=%u on VC %u (last accepted %u)\n",
			       seq, vc, last_seq_vc[vc]);
#if CUBERANGE_COMM_LINK_STATS
			link_frames_refused++;
#endif
			return;
		}
	}
	have_vc[vc] = 1;
	last_seq_vc[vc] = seq;
#else
	static int have_last;
	static uint8_t last_seq;

	if (have_last) {
		int8_t ahead = (int8_t)(seq - last_seq);

		if (ahead <= 0) {
			printk("COMM: REJECTED replayed frame seq=%u (last accepted %u)\n",
			       seq, last_seq);
#if CUBERANGE_COMM_LINK_STATS
			link_frames_refused++;
#endif
			return;
		}
	}
	have_last = 1;
	last_seq = seq;
#endif
#endif

	printk("COMM: uplink frame seq=%u carrying %u octets -> OBC\n",
	       seq, (unsigned int)packet_len);

	csp_packet_t *out = csp_buffer_get(packet_len);

	if (out == NULL) {
		printk("COMM: no CSP buffer for a %u octet packet\n", (unsigned int)packet_len);
		return;
	}
	memcpy(out->data, packet, packet_len);
	out->length = (uint16_t)packet_len;

	/* Connection-oriented, not csp_sendto(). The OBC receives with csp_bind/listen/accept, and a
	 * connectionless packet never reaches a listening socket - it needs csp_bind_callback. The
	 * two APIs look interchangeable and are not; pairing them wrongly loses the packet silently,
	 * which is exactly what the first run of the P0 round trip did. */
	csp_conn_t *conn = csp_connect(CSP_PRIO_NORM, OBC_ADDR, CSP_PORT_PUS, 1000, CSP_O_NONE);

	if (conn == NULL) {
		printk("COMM: no CSP connection to the OBC\n");
		csp_buffer_free(out);
		return;
	}
	csp_send(conn, out);
	csp_close(conn);
	printk("COMM: forwarded %u octets to OBC on port %d\n",
	       (unsigned int)packet_len, CSP_PORT_PUS);
}

static void router_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	while (1) {
		csp_route_work();
	}
}

static void link_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	cr_deframer_t deframer;

	cr_deframer_init(&deframer);

	while (1) {
		uint8_t byte;

		if (k_msgq_get(&link_rx_q, &byte, K_FOREVER) == 0) {
			cr_deframer_feed(&deframer, &byte, 1, on_tc_frame, NULL);
		}
	}
}

/* Downlink: anything the OBC sends to CSP port 17 becomes a TM frame on the link. */
static void down_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	csp_socket_t sock = {0};

	/* CSP_ANY so COMM also answers libcsp's standard services; see the OBC for why. */
	csp_bind(&sock, CSP_ANY);
	csp_listen(&sock, 4);

	static uint8_t frame[CR_MAX_FRAME_LEN];
	static uint8_t wire[CR_MAX_FRAME_LEN + 8];
	uint8_t mc = 0, vc = 0;

	while (1) {
		csp_conn_t *conn = csp_accept(&sock, 1000);

		if (conn == NULL) {
			continue;
		}
		csp_packet_t *packet;

		while ((packet = csp_read(conn, 100)) != NULL) {
			if (csp_conn_dport(conn) != CSP_PORT_PUS) {
				csp_service_handler(packet);   /* takes ownership */
				continue;
			}
			int n = cr_encode_tm_frame(frame, sizeof(frame),
						   packet->data, packet->length, mc++, vc++);
			if (n > 0) {
				size_t total = cr_wrap(wire, sizeof(wire), frame, (size_t)n);

				link_write(wire, total);
				printk("COMM: downlink %u octets from node %d\n",
				       (unsigned int)packet->length, csp_conn_src(conn));
			} else {
				printk("COMM: TM payload of %u octets does not fit a frame\n",
				       (unsigned int)packet->length);
			}
			csp_buffer_free(packet);
		}
		csp_close(conn);
	}
}

K_THREAD_DEFINE(router_id, ROUTER_STACK, router_task, NULL, NULL, NULL, 0, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(link_id, LINK_STACK, link_task, NULL, NULL, NULL, 1, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(down_id, DOWN_STACK, down_task, NULL, NULL, NULL, 1, 0, K_TICKS_FOREVER);

#if CUBERANGE_COMM_CROSSLINK
/* Prove the crosslink carries traffic, once, at startup.
 *
 * Without this the link's presence is an assertion: the interface registers, the console says it
 * is up, and nothing has crossed it. csp_ping is libcsp's own service - csp_services.c is in the
 * library's source list, and `arm-zephyr-eabi-nm` on a COMM image built before this showed no
 * csp_ping symbol at all, because nothing referenced it and --gc-sections dropped it. Referencing
 * it here is what links it in.
 *
 * It is also what the exercise opens with. A crosslink exists so that a spacecraft out of contact
 * with every ground station is reachable through a neighbour that is not; the first thing a
 * student should see is the link working, before seeing what else it carries.
 *
 * The delay is because the peer may still be booting. One shot, not a beacon: a periodic thread
 * would be more firmware than this needs, and a single logged round trip is the whole claim.
 */
static void crosslink_hello(void)
{
	/* Every other spacecraft's COMM. The address plan puts the spacecraft in the top two bits
	 * of the five, so this is just "every other value of those two bits" - see the routing
	 * comment in main(). */
	k_sleep(K_SECONDS(3));

	for (int sat = 0; sat < 4; sat++) {
		uint16_t peer = (uint16_t)(sat * 8 + 5);

		if (peer == COMM_ADDR) {
			continue;
		}
		int ms = csp_ping(peer, 1000, 4, CSP_O_NONE);

		if (ms >= 0) {
			printk("COMM: crosslink reached COMM %u in %d ms\n",
			       (unsigned int)peer, ms);
		}
	}
}
#endif

int main(void)
{
	printk("CUBERANGE: COMM (addr %d) booting\n", COMM_ADDR);

	/* Pin CSP v1. libcsp defaults csp_conf.version to 2 (src/csp_init.c:18) and selects the
	 * header and CFP layouts from it at RUNTIME, so "we use CSP v1" is not true unless it is set
	 * here. Both versions interoperate with themselves, which is why a v2 satellite talking to a
	 * v2 satellite looks perfectly healthy right up until a v1 tool tries to join the bus - the
	 * frames are simply ignored, with no error anywhere. The ground tooling and the golden vectors
	 * in tests/golden/csp.json are v1; the 48-bit v2 header is not wire-compatible. */
	csp_conf.version = 1;
	csp_init();
	k_thread_start(router_id);

	const struct device *can_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_canbus));

	if (!device_is_ready(can_dev)) {
		printk("COMM: FATAL can device not ready\n");
		return -1;
	}
	/* filter_addr 0x3FFF with filter_mask 0 accepts every address, which libcsp's own Zephyr
	 * sample documents as the "receive all packets" setting. Filtering on our own address with
	 * mask 0x3FFF silently dropped everything between addresses 5 and 1 - the mask is 14 bits
	 * wide for CSP v2 while a v1 header carries a 5-bit address, so the comparison lands on
	 * neighbouring bits and succeeds or fails depending on the address pair. Accepting all is
	 * also what a security range wants: a node must be able to see traffic not addressed to it,
	 * the way an attacker or a compromised subsystem would. */
	int err = csp_can_open_and_add_interface(can_dev, "CAN", COMM_ADDR, CAN_BITRATE,
						 0x3FFF, 0x0000, &can_iface);
	if (err != CSP_ERR_NONE) {
		printk("COMM: FATAL csp_can_open_and_add_interface -> %d\n", err);
		return -1;
	}
#if CUBERANGE_COMM_CROSSLINK
	/* The crosslink. fdcan2, joined to every other spacecraft's fdcan2 on one shared hub.
	 *
	 * ROUTING, WITHOUT A ROUTING TABLE. This build sets CONFIG_CSP_USE_RTABLE=n, so csp_io.c
	 * chooses an interface by subnet (csp_iflist_get_by_subnet) and falls back to whatever is
	 * marked default. That is enough for a constellation, because this range's address plan
	 * already puts the spacecraft in the address:
	 *
	 *     CSP v1 address        b4 b3 | b2 b1 b0
	 *                           sat   | node
	 *
	 * identity.py uses STRIDE 8 and MAX_SATELLITES 4, and 8 x 4 is 32, which is the whole
	 * five-bit space (CSP_ID1_HOST_SIZE is 5 in libcsp's csp_id.c). So the top two bits ARE the
	 * spacecraft index, a /2 on the intra-spacecraft interface is exactly "my own spacecraft",
	 * and everything else falls through to here. Nobody designed that: the address plan was laid
	 * out for identity, before this link existed, and it turned out to be the right shape.
	 *
	 * csp_iflist_is_within_subnet builds its mask as ((1 << netmask) - 1) << (5 - netmask), so
	 * netmask 2 is 0b11000 - read the source rather than assume, because the same field means a
	 * host-bits count in some stacks and a prefix length in others.
	 *
	 * The intra interface stops being default. csp_send_direct walks EVERY default interface and
	 * sends a copy to each, so leaving both default would put every internal packet on the
	 * crosslink as well - the spacecraft's own housekeeping, broadcast to the constellation.
	 */
	can_iface->netmask = 2;
	can_iface->is_default = 0;

	const struct device *xlink_dev = DEVICE_DT_GET(DT_NODELABEL(fdcan2));

	if (!device_is_ready(xlink_dev)) {
		printk("COMM: FATAL crosslink device not ready\n");
		return -1;
	}
	/* Same all-accepting filter as the intra bus, for the same reason, plus one specific to this
	 * link: a spacecraft must be able to HEAR its neighbours' traffic. That is what makes the
	 * crosslink a shared medium rather than a bundle of point-to-point wires, and it is what the
	 * exercise on this link is about. */
	err = csp_can_open_and_add_interface(xlink_dev, "XLINK", XLINK_ADDR, CAN_BITRATE,
					     0x3FFF, 0x0000, &xlink_iface);
	if (err != CSP_ERR_NONE) {
		printk("COMM: FATAL crosslink csp_can_open_and_add_interface -> %d\n", err);
		return -1;
	}
	/* A HOST ROUTE, not a subnet, and the address is XLINK_ADDR rather than this node's own.
	 *
	 * Both of those are forced by libcsp's split horizon, which appears three times in csp_io.c
	 * and asks the same question each time:
	 *
	 *     if (csp_iflist_is_within_subnet(iface->addr, routed_from)) continue;
	 *
	 * That compares the OUTGOING interface's ADDRESS against the INCOMING interface's subnet. A
	 * router whose two interfaces both carry the node's own address answers yes for every
	 * netmask - csp_iflist.c:21 builds the mask from the netmask, and netmask 0 builds mask 0,
	 * which makes every address equal to every other - so it forwards NOTHING. There is no error
	 * and no counter: the packet arrives on fdcan2 and stops. Measured, after the first version
	 * of this file did exactly that.
	 *
	 * So the crosslink attachment gets its own address (8i+6, free in the plan) and a /5, which
	 * is a route to itself alone. Outbound traffic does not need the subnet: this is the default
	 * interface, and a packet this node ORIGINATES has routed_from == NULL, which
	 * csp_iflist_is_within_subnet answers 0 for - no split horizon on anything we send.
	 */
	xlink_iface->netmask = 5;
	xlink_iface->is_default = 1;

	printk("CUBERANGE: COMM crosslink up on %s, /2 local, default out\n", xlink_dev->name);
#else
	/* One bus, everything on it. What every exercise except EX-X01 runs. */
	can_iface->is_default = 1;
#endif

	link_dev = DEVICE_DT_GET(DT_ALIAS(spacelink));
	if (!device_is_ready(link_dev)) {
		printk("COMM: FATAL space link device not ready\n");
		return -1;
	}
	uart_irq_callback_user_data_set(link_dev, link_isr, NULL);
	uart_irq_rx_enable(link_dev);

	k_thread_start(link_id);
	k_thread_start(down_id);
#if CUBERANGE_COMM_LINK_STATS
	/* K_TICKS_FOREVER on the definition means the thread exists and does not run until
	 * something starts it. The first version of this omitted the line and produced a build that
	 * counted twenty refusals correctly and reported none of them - a silent half-feature, which
	 * is the shape W47 keeps taking here. Caught by measuring, not by reading. */
	k_thread_start(stats_id);
#endif

#if CUBERANGE_COMM_SDLS
	sdls_selftest();
#endif
	printk("CUBERANGE: COMM ready, link on %s\n", link_dev->name);

#if CUBERANGE_COMM_CROSSLINK
	crosslink_hello();
#endif

	return 0;
}
