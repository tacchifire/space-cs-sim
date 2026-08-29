/*
 * CubeRange walking skeleton: CSP over CAN between two emulated satellite nodes.
 *
 * One image, two roles, selected at build time by CUBERANGE_NODE_ADDR. The node whose address
 * matches CUBERANGE_CLIENT_ADDR also pings its peer; every node answers pings, because libcsp's
 * csp_service_handler implements the CMP ping service for us.
 *
 * Deliberately uses DT_CHOSEN(zephyr_canbus) rather than DT_NODELABEL(can0). libcsp's own Zephyr
 * sample hardcodes can0, which does not exist on nucleo_h753zi - its controller is fdcan1. The
 * board's devicetree already declares `zephyr,canbus = &fdcan1`, so the chosen node is both
 * correct here and portable to any board that declares a CAN bus.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/sys/printk.h>
#include <zephyr/drivers/uart.h>

#include <csp/csp.h>
#include <csp/drivers/can_zephyr.h>

#ifndef CUBERANGE_NODE_ADDR
#define CUBERANGE_NODE_ADDR 1
#endif
#ifndef CUBERANGE_PEER_ADDR
#define CUBERANGE_PEER_ADDR 2
#endif
#ifndef CUBERANGE_CLIENT_ADDR
#define CUBERANGE_CLIENT_ADDR 1
#endif

#define CAN_BITRATE   1000000
#define CSP_PORT_ECHO 10

#define ROUTER_STACK 1024
#define SERVER_STACK 1024
#define ROUTER_PRIO  0
#define SERVER_PRIO  1

static csp_iface_t *can_iface;

static void router_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);
	while (1) {
		csp_route_work();
	}
}

static void server_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	csp_socket_t sock = {0};
	csp_bind(&sock, CSP_ANY);
	csp_listen(&sock, 4);
	printk("CUBERANGE: node %d listening\n", CUBERANGE_NODE_ADDR);

	while (1) {
		csp_conn_t *conn = csp_accept(&sock, 1000);
		if (conn == NULL) {
			continue;
		}
		csp_packet_t *packet;
		while ((packet = csp_read(conn, 50)) != NULL) {
			switch (csp_conn_dport(conn)) {
			case CSP_PORT_ECHO:
				printk("CUBERANGE: node %d echo %u bytes from %d\n",
				       CUBERANGE_NODE_ADDR, packet->length, csp_conn_src(conn));
				csp_send(conn, packet);
				break;
			default:
				/* Ping, uptime, memfree and friends are handled here. */
				csp_service_handler(packet);
				break;
			}
		}
		csp_close(conn);
	}
}

K_THREAD_DEFINE(router_id, ROUTER_STACK, router_task, NULL, NULL, NULL,
		ROUTER_PRIO, 0, K_TICKS_FOREVER);
K_THREAD_DEFINE(server_id, SERVER_STACK, server_task, NULL, NULL, NULL,
		SERVER_PRIO, 0, K_TICKS_FOREVER);

int main(void)
{
	printk("CUBERANGE: node %d booting (peer %d)\n",
	       CUBERANGE_NODE_ADDR, CUBERANGE_PEER_ADDR);

	csp_init();
	k_thread_start(router_id);

	const struct device *can_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_canbus));

	if (!device_is_ready(can_dev)) {
		printk("CUBERANGE: FATAL can device not ready\n");
		return -1;
	}

	/* filter_mask 0 accepts every address, so a node can see traffic that is not addressed to
	 * it. That is deliberate: this is a security range, and an exercise needs to be able to
	 * observe the bus the way an attacker or a compromised subsystem would. */
	int err = csp_can_open_and_add_interface(can_dev, "CAN", CUBERANGE_NODE_ADDR,
						 CAN_BITRATE, CUBERANGE_NODE_ADDR, 0x3FFF,
						 &can_iface);
	if (err != CSP_ERR_NONE) {
		printk("CUBERANGE: FATAL csp_can_open_and_add_interface -> %d\n", err);
		return -1;
	}
	can_iface->is_default = 1;
	printk("CUBERANGE: node %d CAN interface up on %s\n",
	       CUBERANGE_NODE_ADDR, can_dev->name);

	k_thread_start(server_id);

	/* The space link lives on its own UART (see boards/nucleo_h753zi.overlay). Writing a marker
	 * here proves the console and the link are genuinely separate streams - the marker must
	 * appear on the link and NOWHERE on the console. */
#if DT_NODE_HAS_STATUS(DT_ALIAS(spacelink), okay)
	const struct device *link = DEVICE_DT_GET(DT_ALIAS(spacelink));
	if (device_is_ready(link)) {
		const char *marker = "CUBERANGE-LINK\r\n";
		for (const char *p = marker; *p; p++) {
			uart_poll_out(link, *p);
		}
		printk("CUBERANGE: node %d space link up on %s\n",
		       CUBERANGE_NODE_ADDR, link->name);
	} else {
		printk("CUBERANGE: space link device not ready\n");
	}
#else
	printk("CUBERANGE: no spacelink alias in devicetree\n");
#endif

	if (CUBERANGE_NODE_ADDR != CUBERANGE_CLIENT_ADDR) {
		/* Server-only node: nothing else to do on this thread. */
		return 0;
	}

	/* Give the peer time to bring its own interface up before the first ping. */
	k_sleep(K_MSEC(500));

	for (unsigned int i = 0; i < 20; i++) {
		int ms = csp_ping(CUBERANGE_PEER_ADDR, 1000, 8, CSP_O_NONE);
		if (ms >= 0) {
			printk("CUBERANGE: ping %u -> node %d ok (%d ms)\n",
			       i, CUBERANGE_PEER_ADDR, ms);
		} else {
			printk("CUBERANGE: ping %u -> node %d TIMEOUT\n",
			       i, CUBERANGE_PEER_ADDR);
		}
		k_sleep(K_MSEC(200));
	}
	printk("CUBERANGE: client finished\n");
	return 0;
}
