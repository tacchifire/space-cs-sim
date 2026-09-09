/*
 * CubeRange ADCS node: attitude determination and control.
 *
 * Owns the magnetorquers. A torque command arriving on the internal bus changes the body rate, and
 * a large enough rate means the solar panels stop pointing at the sun - the satellite is alive,
 * answering, and slowly running its battery flat. That is the point of exercise EX-A01, and it is
 * deliberately a different SHAPE of consequence from EX-B01: the EPS attack silences the spacecraft
 * immediately and obviously, this one leaves every symptom looking normal for a long time.
 *
 * The vulnerable and mitigated images differ by exactly one thing: whether a commanded torque is
 * checked against the actuator's real authority. Same board, same prj.conf, same sources, same
 * compiler flags - tools/config_diff_gate.py proves that mechanically.
 *
 * WHAT IS AND IS NOT MODELLED. The body rate here is a first-order integrator over commanded
 * torque with a fixed inertia, in one axis, with no disturbance, no gravity gradient, no sensor and
 * no closed loop. It is not attitude dynamics; it is the smallest state that makes an
 * out-of-authority command have a visible physical consequence. ASSURANCE.md's rule applies: a
 * result here says something about the command path, and nothing about a spacecraft's real
 * response.
 *
 * SPDX-License-Identifier: Apache-2.0
 */
#include <zephyr/kernel.h>
#include <zephyr/device.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>
#include <string.h>
#include <stdlib.h>

#include <csp/csp.h>
#include <csp/drivers/can_zephyr.h>

#ifndef ADCS_ADDR
#define ADCS_ADDR 4
#endif
#define CSP_PORT_PUS    10
#define CSP_PORT_ATT    12
#define CAN_BITRATE     1000000

#define ROUTER_STACK 1024
#define APP_STACK    2048
#define CTRL_STACK   1024

/* Control loop period. Body rate is integrated at this cadence, so it is also the resolution of
 * the physical consequence. 100 ms is fast enough that a learner sees the rate move within a few
 * seconds of virtual time and slow enough that the node costs nothing when idle. */
#define CTRL_PERIOD_MS  100

/* Attitude model, in millidegrees per second and milli-newton-metres.
 *
 * TORQUE_TO_RATE_MDPS is the rate change one unit of commanded torque produces per control period.
 * The number is chosen for legibility, not fidelity: at the actuator's real authority the rate
 * moves in units a learner can follow, and an out-of-authority command runs away immediately.
 *
 * TUMBLE_THRESHOLD_MDPS is where the panels stop producing usefully. Crossing it raises the tumble
 * GPIO, which is what the host observes - the same mechanism the EPS rail uses, for the same
 * reason: telemetry can be forged, a pin the firmware drove cannot.
 *
 * TORQUE_BURST_PERIODS is why a command is a burst rather than a standing setpoint. A magnetorquer
 * is commanded for a dwell and then stops; leaving the torque applied forever would mean that even
 * a command well inside the actuator's authority eventually spins the spacecraft up, and then the
 * mitigated build could not pass its third assertion - that a legitimate command still does its
 * job. The bug in this exercise is the missing bound on MAGNITUDE, and modelling the dwell keeps it
 * that way instead of quietly making duration the bug too.
 *
 * Arithmetic, so the numbers are checkable rather than tuned until the test passed:
 *   legitimate, at full authority   20 mNm x 10 x 20 =      4,000 mdps   (below the threshold)
 *   forged, one control period      30000 mNm x 10   =    300,000 mdps   (15x the threshold)
 * so the attack crosses on its first tick and the legitimate slew has a 5x margin. */
#define TORQUE_TO_RATE_MDPS   10
#define TUMBLE_THRESHOLD_MDPS 20000
#define TORQUE_BURST_PERIODS  20

/* The magnetorquer's actual authority. A real coil saturates; commanding past this is not a
 * stronger slew, it is a command the hardware cannot execute. The mitigated build knows that and
 * the vulnerable build does not. */
#define TORQUE_AUTHORITY_MNM  20

/* Attitude command, 8 octets on the wire:
 *   [0]   opcode      1 = set torque
 *   [1]   axis        0 = Z (the only axis modelled)
 *   [2:4] torque      int16 big-endian, milli-newton-metres, signed
 *   [4:8] reserved    zero
 *
 * The torque is int16 on the wire deliberately. The authority is 20 mNm and the field holds
 * +-32767, so the protocol itself offers the attacker three orders of magnitude of headroom - which
 * is what makes the missing check exploitable rather than theoretical. Widening a field beyond what
 * the actuator can do is a real and common design smell. */
#define ATT_CMD_LEN     8
#define ATT_OP_SET_TORQUE 1
#define AXIS_Z          0

static const struct gpio_dt_spec tumble_flag = GPIO_DT_SPEC_GET(DT_ALIAS(tumbleflag), gpios);
static csp_iface_t *can_iface;

static int32_t commanded_torque_mnm;   /* torque being applied during the current burst */
static int32_t burst_left;             /* control periods remaining in the burst */
static int32_t body_rate_mdps;         /* integrated body rate, signed */
static bool tumbling;

static void set_tumble(bool on, const char *why)
{
	if (on == tumbling) {
		return;
	}
	tumbling = on;
	gpio_pin_set_dt(&tumble_flag, on ? 1 : 0);
	printk("ADCS: %s (%s, rate %d mdps)\n",
	       on ? "TUMBLING - panels off sun" : "attitude recovered", why, body_rate_mdps);
}

static void handle_attitude_command(const uint8_t *data, size_t len, uint16_t src)
{
	if (len < ATT_CMD_LEN) {
		printk("ADCS: attitude command too short (%u octets)\n", (unsigned int)len);
		return;
	}
	if (data[0] != ATT_OP_SET_TORQUE) {
		printk("ADCS: unknown attitude opcode %u\n", data[0]);
		return;
	}
	if (data[1] != AXIS_Z) {
		printk("ADCS: unknown axis %u\n", data[1]);
		return;
	}

	int32_t torque = (int16_t)((data[2] << 8) | data[3]);

#if CUBERANGE_ADCS_TORQUE_LIMIT
	/* The whole mitigation. A command outside the actuator's authority is not clamped, it is
	 * refused: clamping would execute a command the operator did not give, and on a spacecraft
	 * the difference between "did less than asked" and "refused and said so" is the difference
	 * between a confusing anomaly and a diagnosable one. */
	if (torque > TORQUE_AUTHORITY_MNM || torque < -TORQUE_AUTHORITY_MNM) {
		printk("ADCS: REJECTED out-of-authority torque %d mNm from node %u (limit +-%d)\n",
		       torque, src, TORQUE_AUTHORITY_MNM);
		return;
	}
#else
	/* No range check. The field is int16, so anything up to +-32767 mNm is accepted and
	 * integrated as if the coil could deliver it. */
	(void)src;
#endif

	commanded_torque_mnm = torque;
	burst_left = TORQUE_BURST_PERIODS;
	printk("ADCS: torque %d mNm accepted from node %u (burst of %d periods)\n",
	       torque, src, TORQUE_BURST_PERIODS);
}

static void control_task(void *a, void *b, void *c)
{
	ARG_UNUSED(a); ARG_UNUSED(b); ARG_UNUSED(c);

	while (1) {
		k_sleep(K_MSEC(CTRL_PERIOD_MS));

		if (burst_left > 0) {
			body_rate_mdps += commanded_torque_mnm * TORQUE_TO_RATE_MDPS;
			if (--burst_left == 0) {
				commanded_torque_mnm = 0;
			}
		}
		/* No damping. A spacecraft with no atmosphere and no active control keeps the rate it
		 * was given, so the consequence persists until somebody commands it away - which is
		 * what makes it observable long after the frame that caused it. */

		/* Saturate the state rather than let it wrap. A wrapped int32 would send the rate
		 * negative and the tumble flag would clear itself, which would read as the satellite
		 * recovering on its own - a false recovery is worse than an obviously pegged value. */
		if (body_rate_mdps > 2000000) {
			body_rate_mdps = 2000000;
		} else if (body_rate_mdps < -2000000) {
			body_rate_mdps = -2000000;
		}

		int32_t magnitude = body_rate_mdps < 0 ? -body_rate_mdps : body_rate_mdps;

		set_tumble(magnitude >= TUMBLE_THRESHOLD_MDPS, "rate threshold");
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

	csp_bind(&sock, CSP_ANY);
	csp_listen(&sock, 4);
	printk("CUBERANGE: ADCS listening (attitude on CSP port %d, torque limit %s)\n",
	       CSP_PORT_ATT, CUBERANGE_ADCS_TORQUE_LIMIT ? "ENFORCED" : "DISABLED");

	while (1) {
		csp_conn_t *conn = csp_accept(&sock, 1000);

		if (conn == NULL) {
			continue;
		}
		csp_packet_t *packet;

		while ((packet = csp_read(conn, 100)) != NULL) {
			if (csp_conn_dport(conn) == CSP_PORT_ATT) {
				handle_attitude_command(packet->data, packet->length,
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
K_THREAD_DEFINE(ctrl_id, CTRL_STACK, control_task, NULL, NULL, NULL, 2, 0, K_TICKS_FOREVER);

int main(void)
{
	printk("CUBERANGE: ADCS (addr %d) booting\n", ADCS_ADDR);

	if (!gpio_is_ready_dt(&tumble_flag)) {
		printk("ADCS: FATAL tumble GPIO not ready\n");
		return -1;
	}
	/* Starts low: the satellite is pointing correctly until something upsets it. */
	gpio_pin_configure_dt(&tumble_flag, GPIO_OUTPUT_INACTIVE);
	printk("ADCS: attitude nominal (rate 0 mdps)\n");

	/* Pin CSP v1 before csp_init. libcsp defaults csp_conf.version to 2 and selects the header
	 * and CFP layouts from it at RUNTIME, so a node that omits this speaks v2 and is silently
	 * invisible to every v1 peer and every v1 tool - no error, anywhere. See the EPS node. */
	csp_conf.version = 1;
	csp_init();
	k_thread_start(router_id);

	const struct device *can_dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_canbus));

	if (!device_is_ready(can_dev)) {
		printk("ADCS: FATAL can device not ready\n");
		return -1;
	}
	int err = csp_can_open_and_add_interface(can_dev, "CAN", ADCS_ADDR, CAN_BITRATE,
						 0x3FFF, 0x0000, &can_iface);
	if (err != CSP_ERR_NONE) {
		printk("ADCS: FATAL csp_can_open_and_add_interface -> %d\n", err);
		return -1;
	}
	can_iface->is_default = 1;

	k_thread_start(app_id);
	k_thread_start(ctrl_id);
	printk("CUBERANGE: ADCS ready (authority +-%d mNm, tumble above %d mdps)\n",
	       TORQUE_AUTHORITY_MNM, TUMBLE_THRESHOLD_MDPS);
	return 0;
}
