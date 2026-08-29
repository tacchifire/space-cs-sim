/*
 * CubeRange EPS node: electrical power system.
 *
 * Owns the load switches. A power command arriving on the internal bus flips a rail, and cutting
 * the COMM rail silences the satellite - which is the point of exercise EX-B01.
 *
 * The vulnerable and mitigated images differ by exactly one thing: whether a command must carry a
 * valid authentication token. Same board, same prj.conf, same sources, same flags. If an exercise
 * needs the platform weakened to work, it is teaching something that is not true.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>
#include <string.h>

#include <csp/csp.h>
#include <csp/drivers/can_zephyr.h>

#define EPS_ADDR        2
#define CSP_PORT_PUS    10
#define CSP_PORT_POWER  11
#define CAN_BITRATE     1000000

#define ROUTER_STACK 1024
#define APP_STACK    2048

/* Power command, 7 octets on the wire:
 *   [0] opcode      1 = set rail
 *   [1] rail id     0 = COMM
 *   [2] state       0 = off, 1 = on
 *   [3:7] token     checked only when CUBERANGE_EPS_REQUIRE_AUTH is set
 */
#define PWR_CMD_LEN     7
#define PWR_OP_SET_RAIL 1
#define RAIL_COMM       0

#if CUBERANGE_EPS_REQUIRE_AUTH
/* A fixed shared secret is not a real design - it is the cheapest control that makes the
 * difference between "any frame on the bus can kill the radio" and "it cannot", which is the
 * lesson EX-B01 exists to teach. The mitigation write-up says plainly what this does not solve:
 * it is replayable, and it does not survive an attacker who can read the OBC's flash. */
static const uint8_t POWER_TOKEN[4] = {0x5A, 0xC3, 0x11, 0xE7};
#endif

static const struct gpio_dt_spec comm_rail = GPIO_DT_SPEC_GET(DT_ALIAS(commrail), gpios);
static csp_iface_t *can_iface;

static void set_rail(uint8_t rail, uint8_t state, const char *why)
{
	if (rail != RAIL_COMM) {
		printk("EPS: unknown rail %u\n", rail);
		return;
	}
	gpio_pin_set_dt(&comm_rail, state ? 1 : 0);
	printk("EPS: COMM rail %s (%s)\n", state ? "ON" : "OFF", why);
}

static void handle_power_command(const uint8_t *data, size_t len, uint16_t src)
{
	if (len < PWR_CMD_LEN) {
		printk("EPS: power command too short (%u octets)\n", (unsigned int)len);
		return;
	}
	if (data[0] != PWR_OP_SET_RAIL) {
		printk("EPS: unknown power opcode %u\n", data[0]);
		return;
	}

#if CUBERANGE_EPS_REQUIRE_AUTH
	if (memcmp(&data[3], POWER_TOKEN, sizeof(POWER_TOKEN)) != 0) {
		printk("EPS: REJECTED unauthenticated rail command from node %u\n", src);
		return;
	}
#else
	/* No authentication. Any node that can put a frame on the bus can switch a rail, including
	 * one that simply forged the source address. */
	(void)src;
#endif

	set_rail(data[1], data[2], "commanded");
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

	csp_bind(&sock, CSP_ANY);
	csp_listen(&sock, 4);
	printk("CUBERANGE: EPS listening (power on CSP port %d, auth %s)\n",
	       CSP_PORT_POWER, CUBERANGE_EPS_REQUIRE_AUTH ? "REQUIRED" : "DISABLED");

	while (1) {
		csp_conn_t *conn = csp_accept(&sock, 1000);

		if (conn == NULL) {
			continue;
		}
		csp_packet_t *packet;

		while ((packet = csp_read(conn, 100)) != NULL) {
			if (csp_conn_dport(conn) == CSP_PORT_POWER) {
				handle_power_command(packet->data, packet->length,
						     csp_conn_src(conn));
				csp_buffer_free(packet);
			} else {
				csp_service_handler(packet);   /* takes ownership */
			}
		}
		csp_close(conn);
	}
}

K_THREAD_DEFINE(router_id, ROUTER_STACK, router_task, NULL, NULL, NULL, 0, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(app_id, APP_STACK, app_task, NULL, NULL, NULL, 1, 0, K_TICKS_FOREVER);

int main(void)
{
	printk("CUBERANGE: EPS (addr %d) booting\n", EPS_ADDR);

	if (!gpio_is_ready_dt(&comm_rail)) {
		printk("EPS: FATAL COMM rail GPIO not ready\n");
		return -1;
	}
	/* Rails come up powered: the satellite is alive until something turns it off. */
	gpio_pin_configure_dt(&comm_rail, GPIO_OUTPUT_ACTIVE);
	printk("EPS: COMM rail ON (initial)\n");

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
		printk("EPS: FATAL can device not ready\n");
		return -1;
	}
	/* Accept-all filter; see the COMM node for why. */
	int err = csp_can_open_and_add_interface(can_dev, "CAN", EPS_ADDR, CAN_BITRATE,
						 0x3FFF, 0x0000, &can_iface);
	if (err != CSP_ERR_NONE) {
		printk("EPS: FATAL csp_can_open_and_add_interface -> %d\n", err);
		return -1;
	}
	can_iface->is_default = 1;

	k_thread_start(app_id);
	printk("CUBERANGE: EPS ready\n");
	return 0;
}
