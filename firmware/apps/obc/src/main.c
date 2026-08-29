/*
 * CubeRange OBC node: command and data handling.
 *
 * Receives CCSDS Space Packets from COMM over CSP and dispatches by PUS service. P0 implements
 * service 17 (test) only; every other service is logged and ignored, so an unimplemented service
 * is visible rather than silent.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/sys/printk.h>
#include <string.h>

#include <csp/csp.h>
#include <csp/drivers/can_zephyr.h>

#include "cuberange_proto.h"

#define OBC_ADDR      1
#define COMM_ADDR     5
/* CSP port for the PUS pipe. NOT 17: libcsp's CSP_PORT_MAX_BIND defaults to 16, csp_port.c
 * rejects any lookup above it, and ports above 16 are reserved for outgoing ephemeral source
 * ports (conn->sport_outgoing = CSP_PORT_MAX_BIND + 1 + i). Binding 17 to match the PUS service
 * number silently dropped every uplink packet while csp_ping on port 1 kept working - a mnemonic
 * is not worth resizing the library's port table. */
#define CSP_PORT_PUS  10
#define CAN_BITRATE   1000000

#define SP_HEADER_LEN     6
#define PUS_TC_SEC_LEN    5
#define PUS_TM_SEC_LEN    7
#define PUS_VERSION       2
#define SERVICE_TEST      17
#define SUBTYPE_TEST      1
#define SUBTYPE_TEST_REP  2
#define OBC_APID          0x0A9
#define TIME_LEN          4

#define ROUTER_STACK 1024
#define APP_STACK    2048

static csp_iface_t *can_iface;
static uint16_t tm_seq_count;
static uint16_t tm_msg_counter;

/* Build a TM Space Packet carrying a PUS 17,2 report and send it to COMM. */
static void send_test_report(uint16_t source_id)
{
	uint8_t body[SP_HEADER_LEN + PUS_TM_SEC_LEN + TIME_LEN];
	uint32_t now = (uint32_t)k_uptime_get();
	size_t data_len = PUS_TM_SEC_LEN + TIME_LEN;

	uint16_t word0 = (0 << 12) | (1 << 11) | OBC_APID;      /* TM, secondary header present */
	uint16_t word1 = (uint16_t)((0x3u << 14) | (tm_seq_count++ & 0x3FFF));
	uint16_t word2 = (uint16_t)(data_len - 1);

	body[0] = (uint8_t)(word0 >> 8);  body[1] = (uint8_t)(word0 & 0xFF);
	body[2] = (uint8_t)(word1 >> 8);  body[3] = (uint8_t)(word1 & 0xFF);
	body[4] = (uint8_t)(word2 >> 8);  body[5] = (uint8_t)(word2 & 0xFF);

	uint8_t *sec = body + SP_HEADER_LEN;
	uint16_t counter = tm_msg_counter++;

	sec[0] = PUS_VERSION << 4;
	sec[1] = SERVICE_TEST;
	sec[2] = SUBTYPE_TEST_REP;
	sec[3] = (uint8_t)(counter >> 8);   sec[4] = (uint8_t)(counter & 0xFF);
	sec[5] = (uint8_t)(source_id >> 8); sec[6] = (uint8_t)(source_id & 0xFF);
	sec[7] = (uint8_t)(now >> 24); sec[8] = (uint8_t)(now >> 16);
	sec[9] = (uint8_t)(now >> 8);  sec[10] = (uint8_t)(now & 0xFF);

	csp_packet_t *packet = csp_buffer_get(sizeof(body));

	if (packet == NULL) {
		printk("OBC: no CSP buffer for a test report\n");
		return;
	}
	memcpy(packet->data, body, sizeof(body));
	packet->length = (uint16_t)sizeof(body);

	/* Connection-oriented: COMM's downlink task receives with csp_bind/listen/accept. */
	csp_conn_t *conn = csp_connect(CSP_PRIO_NORM, COMM_ADDR, CSP_PORT_PUS, 1000, CSP_O_NONE);

	if (conn == NULL) {
		printk("OBC: no CSP connection to COMM\n");
		csp_buffer_free(packet);
		return;
	}
	csp_send(conn, packet);
	csp_close(conn);
	printk("OBC: PUS 17,2 report sent to COMM (counter %u)\n", counter);
}

static void handle_space_packet(const uint8_t *raw, size_t len)
{
	if (len < SP_HEADER_LEN + PUS_TC_SEC_LEN) {
		printk("OBC: space packet too short: %u octets\n", (unsigned int)len);
		return;
	}
	uint16_t word0 = (uint16_t)((raw[0] << 8) | raw[1]);
	uint16_t apid = word0 & 0x7FF;
	size_t declared = (size_t)((raw[4] << 8) | raw[5]) + 1;

	if (declared != len - SP_HEADER_LEN) {
		printk("OBC: declared data length %u but %u octets follow\n",
		       (unsigned int)declared, (unsigned int)(len - SP_HEADER_LEN));
		return;
	}
	const uint8_t *sec = raw + SP_HEADER_LEN;
	uint8_t service = sec[1];
	uint8_t subtype = sec[2];
	uint16_t source_id = (uint16_t)((sec[3] << 8) | sec[4]);

	printk("OBC: APID 0x%03x PUS %u,%u from source %u\n", apid, service, subtype, source_id);

	if (service == SERVICE_TEST && subtype == SUBTYPE_TEST) {
		send_test_report(source_id);
	} else {
		printk("OBC: service %u,%u is not implemented in P0\n", service, subtype);
	}
}

static void router_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	while (1) {
		csp_route_work();
	}
}

static void app_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	csp_socket_t sock = {0};

	/* CSP_ANY, not just the PUS port: libcsp's standard services (ping, uptime, memfree) arrive
	 * on their own ports, and a node that ignores them is both unhelpful and impossible to probe.
	 * Binding only port 17 made an early diagnostic ping look like a dead bus when the bus was
	 * fine - nobody was listening on the ping port. */
	csp_bind(&sock, CSP_ANY);
	csp_listen(&sock, 4);
	printk("CUBERANGE: OBC listening (PUS on CSP port %d)\n", CSP_PORT_PUS);

	while (1) {
		csp_conn_t *conn = csp_accept(&sock, 1000);

		if (conn == NULL) {
			continue;
		}
		printk("OBC: accepted a connection from %d on port %d\n",
		       csp_conn_src(conn), csp_conn_dport(conn));

		csp_packet_t *packet;

		while ((packet = csp_read(conn, 100)) != NULL) {
			if (csp_conn_dport(conn) == CSP_PORT_PUS) {
				handle_space_packet(packet->data, packet->length);
				csp_buffer_free(packet);
			} else {
				/* Takes ownership of the packet. */
				csp_service_handler(packet);
			}
		}
		csp_close(conn);
	}
}

K_THREAD_DEFINE(router_id, ROUTER_STACK, router_task, NULL, NULL, NULL, 0, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(app_id, APP_STACK, app_task, NULL, NULL, NULL, 1, 0, K_TICKS_FOREVER);

int main(void)
{
	printk("CUBERANGE: OBC (addr %d) booting\n", OBC_ADDR);

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
		printk("OBC: FATAL can device not ready\n");
		return -1;
	}
	/* See the COMM node for why the filter accepts everything rather than just this address. */
	int err = csp_can_open_and_add_interface(can_dev, "CAN", OBC_ADDR, CAN_BITRATE,
						 0x3FFF, 0x0000, &can_iface);
	if (err != CSP_ERR_NONE) {
		printk("OBC: FATAL csp_can_open_and_add_interface -> %d\n", err);
		return -1;
	}
	can_iface->is_default = 1;

	k_thread_start(app_id);
	printk("CUBERANGE: OBC ready\n");
	return 0;
}
