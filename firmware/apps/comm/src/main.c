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

#define RX_RING_SIZE  512
#define ROUTER_STACK  1024
#define LINK_STACK    2048
#define DOWN_STACK    2048

static const struct device *link_dev;
static csp_iface_t *can_iface;

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

/* One deframed TC frame: strip the frame header and hand the Space Packet to the OBC. */
static void on_tc_frame(const uint8_t *frame, size_t len, void *ctx)
{
	ARG_UNUSED(ctx);

	uint8_t seq;
	const uint8_t *packet;
	size_t packet_len;

	if (cr_decode_tc_frame(frame, len, &seq, &packet, &packet_len) != 0) {
		printk("COMM: dropping a TC frame that failed its FECF or length check\n");
		return;
	}
#if CUBERANGE_COMM_ANTIREPLAY
	static int have_last;
	static uint8_t last_seq;

	if (have_last) {
		int8_t ahead = (int8_t)(seq - last_seq);

		if (ahead <= 0) {
			printk("COMM: REJECTED replayed frame seq=%u (last accepted %u)\n",
			       seq, last_seq);
			return;
		}
	}
	have_last = 1;
	last_seq = seq;
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
	can_iface->is_default = 1;

	link_dev = DEVICE_DT_GET(DT_ALIAS(spacelink));
	if (!device_is_ready(link_dev)) {
		printk("COMM: FATAL space link device not ready\n");
		return -1;
	}
	uart_irq_callback_user_data_set(link_dev, link_isr, NULL);
	uart_irq_rx_enable(link_dev);

	k_thread_start(link_id);
	k_thread_start(down_id);

	printk("CUBERANGE: COMM ready, link on %s\n", link_dev->name);

	return 0;
}
