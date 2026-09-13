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
#include <zephyr/drivers/gpio.h>
#include <zephyr/sys/printk.h>
#include <string.h>

#include <csp/csp.h>
#include <csp/drivers/can_zephyr.h>

#include "cuberange_proto.h"

#ifndef OBC_ADDR
#define OBC_ADDR 1
#endif
#ifndef COMM_ADDR
#define COMM_ADDR 5
#endif
/* CSP port for the PUS pipe. NOT 17: libcsp's CSP_PORT_MAX_BIND defaults to 16, csp_port.c
 * rejects any lookup above it, and ports above 16 are reserved for outgoing ephemeral source
 * ports (conn->sport_outgoing = CSP_PORT_MAX_BIND + 1 + i). Binding 17 to match the PUS service
 * number silently dropped every uplink packet while csp_ping on port 1 kept working - a mnemonic
 * is not worth resizing the library's port table. */
#ifndef CUBERANGE_OBC_REQUIRE_PUS_AUTH
#define CUBERANGE_OBC_REQUIRE_PUS_AUTH 0
#endif

/* Included after the default above, never beside the other headers: a `#if` on a macro that has
 * not been defaulted yet is 0 whatever the build says, and the symptom is a build with the flag
 * ON whose verifier was compiled out. Measured the hard way in COMM - see W47. */
#if CUBERANGE_OBC_REQUIRE_PUS_AUTH
#include "cuberange_pus_auth.h"
#include "cuberange_keys.h"
#include <mbedtls/gcm.h>
#endif

#define CSP_PORT_PUS  10
#define CAN_BITRATE   1000000

#define SP_HEADER_LEN     6
#define PUS_TC_SEC_LEN    5
#define PUS_TM_SEC_LEN    7
#define PUS_VERSION       2
#define SERVICE_TEST      17
#define SUBTYPE_TEST      1
#define SUBTYPE_TEST_REP  2
#define SERVICE_FUNCTION  8
#define SUBTYPE_PERFORM   1

/* Function management. Function 1 switches the COMM power rail, which the OBC executes by sending
 * an authenticated command to the EPS - the OBC is one of the nodes that legitimately holds the
 * power token. This is the command EX-L01 replays. */
#define FUNC_SET_COMM_RAIL 1
#define FUNC_MAINTENANCE   9
#define CSP_PORT_POWER     11
#ifndef ADDR_EPS
#define ADDR_EPS           2
#endif
#define PWR_OP_SET_RAIL    1
#define RAIL_COMM          0
static const uint8_t POWER_TOKEN[4] = {0x5A, 0xC3, 0x11, 0xE7};
#ifndef OBC_APID
#define OBC_APID 0x0A9
#endif
#define TIME_LEN          4

/* --------------------------------------------------------------- EX-F01 -----
 *
 * The PUS 8 argument block is copied into a frame-local buffer before it is parsed. That is an
 * ordinary shape - a handler that wants a stable, aligned copy of its arguments - and the
 * vulnerable build sizes the copy from the packet rather than from the buffer.
 *
 * What this can and cannot be exploited into is settled by measurement, not by preference. Renode
 * enforces the MPU exactly as the part does: SRAM is execute-never, so PC landing in the buffer
 * executes nothing and takes a MemManage fault. probe.sh section G reproduces that. Teaching
 * shellcode here would be teaching something false, so the exercise is code reuse - and the
 * defaults that make it work are the real ones, not a weakened build: Zephyr has ARM_MPU,
 * HW_STACK_PROTECTION and MPU_STACK_GUARD on, and STACK_CANARIES, STACK_SENTINEL and USERSPACE off.
 * The stack guard sits BELOW the stack, so a copy running upward through the frame never touches
 * it.
 */
#define PUS8_ARG_BUF_LEN  16

static bool fdir_inhibited;
static const struct gpio_dt_spec fdir_flag = GPIO_DT_SPEC_GET(DT_ALIAS(fdirinhibit), gpios);

/* Maintenance handler, disabled in flight.
 *
 * Every spacecraft carries a few of these: a privileged action that made sense on the bench, was
 * never removed, and is not reachable from any command the ground can legally send. It sits in the
 * command table below with its enable bit clear, so the dispatcher refuses it and the linker keeps
 * it. At a fixed address, because the flash is XIP and there is no ASLR - both measured.
 *
 * An earlier version used `__attribute__((used))` plus a volatile function pointer, and
 * --gc-sections removed it anyway: nothing reachable referenced the pointer either, and the
 * "FDIR INHIBITED" string was simply absent from the image. Dead code that ships is the premise of
 * this exercise; dead code the linker removes would make it a fiction. The table is what keeps it
 * honest, and it is also the more realistic shape.
 */
__attribute__((noinline))
static void maintenance_inhibit_fdir(void)
{
	if (!fdir_inhibited) {
		fdir_inhibited = true;
		gpio_pin_set_dt(&fdir_flag, 1);
		printk("OBC: FDIR INHIBITED by maintenance handler\n");
	}
	/* This handler cannot return, and the reason is worth stating rather than hiding.
	 *
	 * It was entered by a return instruction reading a saved link register the attacker chose,
	 * so its own link register still holds that same value: returning re-enters it, forever.
	 * Measured before this line existed - 21 repeats of the message in six seconds, a thread
	 * spinning on a poisoned LR and burning the CPU that everything else on this node shares.
	 *
	 * Parking the task is the least misleading model. A real chain would not stop here: it would
	 * pivot the stack and keep going, which is EX-F01b. This exercise ends at the first gadget,
	 * and says so.
	 */
	k_thread_abort(k_current_get());
}

static void command_comm_rail(uint8_t state);

static void function_set_comm_rail(void)
{
	/* The argument is read from the copied block by the dispatcher's caller. */
}

struct pus8_function {
	uint16_t id;
	bool enabled;
	void (*handler)(void);
};

/* Function 9 is the maintenance entry, and `enabled` is what stands between the ground and it.
 * That is an authorisation check, and it is not the bug in this exercise - the dispatcher honours
 * it faithfully. EX-F01 never calls this table at all. */
static const struct pus8_function FUNCTION_TABLE[] = {
	{ FUNC_SET_COMM_RAIL,   true,  function_set_comm_rail },
	{ FUNC_MAINTENANCE,     false, maintenance_inhibit_fdir },
};

#if CUBERANGE_OBC_REQUIRE_AUTHORITY
/* Which ground station may invoke which function - EX-G02's whole difference.
 *
 * The TC secondary header has carried a 16-bit source id since P0. The spacecraft read it, logged
 * it and echoed it into the report's destination id, and used it to decide nothing. Authority
 * lived only in the ground segment's own bookkeeping, where it governs what an operator is
 * offered rather than what the spacecraft will do.
 *
 * `enabled` in FUNCTION_TABLE is an authorisation check too, and a coarser one: it says nobody may
 * call function 9. This says who may call function 1. Both are needed, and only one existed.
 *
 * Not present at all in the vulnerable build. A table compiled in and never read would be a
 * different defect - and a less honest one, because nobody writes an authority table and then
 * forgets to call it. What happened here is that nobody wrote one.
 */
struct pus8_authority {
	uint16_t source_id;
	uint16_t function_id;
};

static const struct pus8_authority AUTHORITY_TABLE[] = {
	{ GROUND_PRIMARY_ID, FUNC_SET_COMM_RAIL },
	/* GROUND_BACKUP_ID is deliberately absent for FUNC_SET_COMM_RAIL. The backup station may
	 * observe - service 17 is not gated here - and may not switch the spacecraft's own radio
	 * off, which is what the ground segment's authorisation matrix already said and what the
	 * spacecraft was not enforcing. */
};

static bool source_may_perform(uint16_t source_id, uint16_t function_id)
{
	for (size_t i = 0; i < ARRAY_SIZE(AUTHORITY_TABLE); i++) {
		if (AUTHORITY_TABLE[i].source_id == source_id &&
		    AUTHORITY_TABLE[i].function_id == function_id) {
			return true;
		}
	}
	return false;
}
#endif

#if CUBERANGE_OBC_CROSSLINK_ORIGIN
/* Bind the claimed source to the path the packet arrived on - EX-X01's whole difference.
 *
 * The TC secondary header's source id is a FIELD THE SENDER WRITES. EX-G02 added an authority
 * table keyed on it and EX-G04 added a refusal report about it, and both are correct about what
 * they do: they stop a ground station from exceeding its authority. Neither authenticates
 * anything, because there is nothing in the packet to authenticate with.
 *
 * That was survivable while every packet reached this OBC through its own spacecraft's COMM, off
 * the space link. The crosslink is a second way in, it is shared with every other spacecraft in
 * the constellation, and none of the link-layer work - EX-L01's anti-replay, EX-G03's per-VC
 * sequence numbers - exists on it. Those defences live on the space link. A defence is attached
 * to a path, not to an asset.
 *
 * So: a packet claiming to come from a ground station has to have come from this spacecraft's own
 * COMM. A peer may talk to us. A peer may not be the ground.
 *
 * This is a TOPOLOGY check, not authentication. It says where the packet entered, which the
 * attacker does not choose, instead of who sent it, which the attacker writes. It stops a peer
 * from borrowing the ground's name; it does nothing about a peer that compromises our own COMM,
 * and nothing about anyone who can transmit on the space link. Authenticating the sender needs
 * SDLS, which this range does not implement - see ASSURANCE.md, which says so and will keep
 * saying so until it is true.
 */
static bool origin_permits_claim(uint16_t source_id, uint16_t via)
{
	if (source_id != GROUND_PRIMARY_ID && source_id != GROUND_BACKUP_ID) {
		return true;
	}
	return via == COMM_ADDR;
}
#endif

#if CUBERANGE_OBC_REQUIRE_PUS_AUTH
/* Authentication on the REQUEST, which is what makes it independent of the road.
 *
 * EX-X01 and EX-S01 both end at the same place: a control bound to a path protects that path.
 * EX-S01's write-up names three ways out and says the third is the one that actually answers it -
 * put the MAC on the telecommand, so it does not matter which link it arrived on. This is that.
 *
 * The trailer is inside the Space Packet and the packet's own length field covers it, so the same
 * octets verify whether they arrived in a TC transfer frame off the space link, in a CSP packet
 * off the crosslink, or on the internal bus. The OBC checks the request it is about to act on.
 *
 * NOTE WHAT THIS DOES NOT DO. It answers who, never what: EX-G02's authority table is still the
 * thing that decides whether an authenticated station may switch a rail off, and it runs after
 * this. And telemetry is not signed - the reports going the other way carry no trailer, which is
 * a real asymmetry and is written down in EX-S02's mitigation rather than left to be found.
 */
static bool pus_auth_ok(uint8_t *packet, size_t *len)
{
	struct cr_pus_auth_parts parts;

	if (cr_pus_auth_split(packet, *len, &parts) != 0) {
		return false;
	}

	uint8_t nonce[CR_PUS_AUTH_NONCE_LEN];

	cr_pus_auth_nonce(parts.apid, parts.source_id, parts.seq, nonce);

	mbedtls_gcm_context gcm;

	mbedtls_gcm_init(&gcm);
	bool ok = false;

	if (mbedtls_gcm_setkey(&gcm, MBEDTLS_CIPHER_ID_AES, cr_sdls_key,
			       8 * sizeof(cr_sdls_key)) == 0) {
		ok = mbedtls_gcm_auth_decrypt(&gcm, 0, nonce, sizeof(nonce),
					      parts.aad, parts.aad_len,
					      parts.mac, CR_PUS_AUTH_MAC_LEN, NULL, NULL) == 0;
	}
	mbedtls_gcm_free(&gcm);
	if (!ok) {
		return false;
	}

	/* Anti-replay, PER SOURCE ID, because two ground stations are two senders and one counter
	 * between them is EX-G03 a third time. A small table rather than a map: this range has two
	 * stations and a handful of spacecraft, and a linear scan of eight entries on a telecommand
	 * is not the thing to optimise.
	 *
	 * The sequence number is inside the authenticated region, so advancing it invalidates the
	 * MAC - the same property that makes SDLS's counter worth more than EX-L01's. */
	static struct { uint16_t source; uint32_t seen; bool used; } replay[8];
	size_t slot = ARRAY_SIZE(replay);

	for (size_t i = 0; i < ARRAY_SIZE(replay); i++) {
		if (replay[i].used && replay[i].source == parts.source_id) {
			slot = i;
			break;
		}
		if (!replay[i].used && slot == ARRAY_SIZE(replay)) {
			slot = i;
		}
	}
	if (slot == ARRAY_SIZE(replay)) {
		printk("OBC: no replay slot left for source %u - refusing\n",
		       (unsigned int)parts.source_id);
		return false;
	}
	if (replay[slot].used && parts.seq <= replay[slot].seen) {
		printk("OBC: REPLAY - source %u sequence %u, already seen %u\n",
		       (unsigned int)parts.source_id, (unsigned int)parts.seq,
		       (unsigned int)replay[slot].seen);
		return false;
	}
	replay[slot].source = parts.source_id;
	replay[slot].seen = parts.seq;
	replay[slot].used = true;

	cr_pus_auth_strip(packet, *len);
	*len -= CR_PUS_AUTH_TRAILER;
	return true;
}
#endif /* CUBERANGE_OBC_REQUIRE_PUS_AUTH */

#define ROUTER_STACK 1024
/* The PUS-auth build carries a 424-octet mbedtls_gcm_context in pus_auth_ok, which runs on this
 * thread. W47 is what happens when a stack is not sized for one: a node that boots, prints, passes
 * its own crypto self-test and then silently stops working, because the overflow corrupts a kernel
 * object rather than faulting. */
#if CUBERANGE_OBC_REQUIRE_PUS_AUTH
#define APP_STACK    3584
#else
#define APP_STACK    2048
#endif

static csp_iface_t *can_iface;
static uint16_t tm_seq_count;
static uint16_t tm_msg_counter;

/* Build a TM Space Packet carrying a PUS 17,2 report and send it to COMM. */
#if CUBERANGE_OBC_VERIFY_REPORTS
/* PUS 1,2 - acceptance failure (ECSS-E-ST-70-41C 6.1). The spacecraft saying out loud that it
 * refused, and which request it refused.
 *
 * EX-G02 and EX-G03 both end by saying their refusals are printk on a console the ground never
 * sees, so an operator locked out by a misconfigured table and an operator being attacked look
 * identical from the downlink - which is to say invisible. This is the layer both of them named
 * and neither built.
 *
 * The request id is the failed packet's OWN first four octets: version, type, secondary header
 * flag and APID, then sequence flags and count. Nothing is invented and nothing is remembered -
 * the ground matches the report against a request it already has a copy of. Verified against
 * src/cuberange/proto/pus.py: request_id(apid, seq) equals the encoded packet's raw[0..4].
 */
static void send_acceptance_failure(uint16_t dest_id, const uint8_t *failed_packet,
				    uint8_t failure_code)
{
	uint8_t body[SP_HEADER_LEN + PUS_TM_SEC_LEN + TIME_LEN + 5];
	uint32_t now = (uint32_t)k_uptime_get();
	size_t data_len = PUS_TM_SEC_LEN + TIME_LEN + 5;

	uint16_t word0 = (0 << 12) | (1 << 11) | OBC_APID;
	uint16_t word1 = (uint16_t)((0x3u << 14) | (tm_seq_count++ & 0x3FFF));
	uint16_t word2 = (uint16_t)(data_len - 1);

	body[0] = (uint8_t)(word0 >> 8);  body[1] = (uint8_t)(word0 & 0xFF);
	body[2] = (uint8_t)(word1 >> 8);  body[3] = (uint8_t)(word1 & 0xFF);
	body[4] = (uint8_t)(word2 >> 8);  body[5] = (uint8_t)(word2 & 0xFF);

	uint8_t *sec = body + SP_HEADER_LEN;
	uint16_t counter = tm_msg_counter++;

	sec[0] = PUS_VERSION << 4;
	sec[1] = 1;                                    /* service 1, request verification */
	sec[2] = 2;                                    /* subtype 2, acceptance failure   */
	sec[3] = (uint8_t)(counter >> 8);   sec[4] = (uint8_t)(counter & 0xFF);
	sec[5] = (uint8_t)(dest_id >> 8);   sec[6] = (uint8_t)(dest_id & 0xFF);
	sec[7] = (uint8_t)(now >> 24); sec[8] = (uint8_t)(now >> 16);
	sec[9] = (uint8_t)(now >> 8);  sec[10] = (uint8_t)(now & 0xFF);

	uint8_t *app = sec + PUS_TM_SEC_LEN + TIME_LEN;

	memcpy(app, failed_packet, 4);                 /* the request id, verbatim */
	app[4] = failure_code;

	csp_packet_t *packet = csp_buffer_get(sizeof(body));

	if (packet == NULL) {
		printk("OBC: no CSP buffer for an acceptance failure report\n");
		return;
	}
	memcpy(packet->data, body, sizeof(body));
	packet->length = (uint16_t)sizeof(body);

	csp_conn_t *conn = csp_connect(CSP_PRIO_NORM, COMM_ADDR, CSP_PORT_PUS, 1000, CSP_O_NONE);

	if (conn == NULL) {
		printk("OBC: no CSP connection to COMM for the failure report\n");
		csp_buffer_free(packet);
		return;
	}
	csp_send(conn, packet);
	csp_close(conn);
	printk("OBC: PUS 1,2 acceptance failure reported to source %u (code %u)\n",
	       dest_id, failure_code);
}
#endif

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

/* Ask the EPS to switch the COMM rail, with the token the mitigated EPS requires. */
static void command_comm_rail(uint8_t state)
{
	uint8_t body[7] = {PWR_OP_SET_RAIL, RAIL_COMM, state, 0, 0, 0, 0};

	memcpy(&body[3], POWER_TOKEN, sizeof(POWER_TOKEN));

	csp_packet_t *packet = csp_buffer_get(sizeof(body));

	if (packet == NULL) {
		printk("OBC: no CSP buffer for a rail command\n");
		return;
	}
	memcpy(packet->data, body, sizeof(body));
	packet->length = (uint16_t)sizeof(body);

	csp_conn_t *conn = csp_connect(CSP_PRIO_NORM, ADDR_EPS, CSP_PORT_POWER, 1000, CSP_O_NONE);

	if (conn == NULL) {
		printk("OBC: no CSP connection to EPS\n");
		csp_buffer_free(packet);
		return;
	}
	csp_send(conn, packet);
	csp_close(conn);
	printk("OBC: PUS 8 executed - COMM rail %s\n", state ? "ON" : "OFF");
}

/* noinline so the handler owns a frame with a saved return address. Inlining is not a security
 * control and this is set for both builds, so the pair stays identical apart from the flag. */
__attribute__((noinline))
static void handle_function(const uint8_t *app_data, size_t len, uint16_t source_id,
			    const uint8_t *failed_request_id)
{
	uint8_t args[PUS8_ARG_BUF_LEN];

	if (len < 3) {
		printk("OBC: PUS 8 argument block too short (%u octets)\n", (unsigned int)len);
		return;
	}
	uint16_t function_id = (uint16_t)((app_data[0] << 8) | app_data[1]);
	size_t arg_len = len - 2;

#if CUBERANGE_OBC_REQUIRE_AUTHORITY
	/* Before the copy, not after. Deciding whether to act on a request is not a thing to do
	 * once the request's data is already in a local buffer. */
	if (!source_may_perform(source_id, function_id)) {
		printk("OBC: REJECTED PUS 8 function %u from source %u - not authorised\n",
		       (unsigned int)function_id, (unsigned int)source_id);
#if CUBERANGE_OBC_VERIFY_REPORTS
		send_acceptance_failure(source_id, failed_request_id, 1 /* not authorised */);
#endif
		return;
	}
#else
	ARG_UNUSED(source_id);
#endif

#if CUBERANGE_OBC_PUS8_LENGTH_CHECK
	/* The whole mitigation. One line, and it is the difference between a range exercise and a
	 * control-flow hijack. */
	if (arg_len > sizeof(args)) {
		printk("OBC: REJECTED PUS 8 argument block of %u octets (buffer is %u)\n",
		       (unsigned int)arg_len, (unsigned int)sizeof(args));
		return;
	}
#endif
	/* The length comes from the packet. Nothing above bounds it against the buffer. */
	memcpy(args, &app_data[2], arg_len);

	for (size_t i = 0; i < ARRAY_SIZE(FUNCTION_TABLE); i++) {
		if (FUNCTION_TABLE[i].id != function_id) {
			continue;
		}
		if (!FUNCTION_TABLE[i].enabled) {
			printk("OBC: function %u is disabled in flight\n", function_id);
			return;
		}
		if (function_id == FUNC_SET_COMM_RAIL) {
			command_comm_rail(args[0]);
		} else {
			FUNCTION_TABLE[i].handler();
		}
		return;
	}
	printk("OBC: unknown function %u\n", function_id);
}

static void handle_space_packet(const uint8_t *raw, size_t len, uint16_t via)
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

#if CUBERANGE_OBC_CROSSLINK_ORIGIN
	/* Before the dispatch, and before the authority table, because this is not a question about
	 * what the sender may do. It is a question about whether the name on the packet can be the
	 * name it says, and a packet that fails it must not reach a handler at all. */
	if (!origin_permits_claim(source_id, via)) {
		printk("OBC: REJECTED a packet claiming source %u that arrived from node %u\n",
		       (unsigned int)source_id, (unsigned int)via);
#if CUBERANGE_OBC_VERIFY_REPORTS
		/* Reported to the claimed source, not to the sender. A ground station receiving a
		 * refusal for a request id it never issued is being told its name is in use. */
		send_acceptance_failure(source_id, raw, 4 /* wrong origin */);
#endif
		return;
	}
#else
	ARG_UNUSED(via);
#endif

	if (service == SERVICE_TEST && subtype == SUBTYPE_TEST) {
		send_test_report(source_id);
	} else if (service == SERVICE_FUNCTION && subtype == SUBTYPE_PERFORM) {
		/* `raw` is the Space Packet, and its first four octets ARE the request id. */
		handle_function(sec + PUS_TC_SEC_LEN, len - SP_HEADER_LEN - PUS_TC_SEC_LEN,
				source_id, raw);
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
				size_t plen = packet->length;
#if CUBERANGE_OBC_REQUIRE_PUS_AUTH
				/* BEFORE handle_space_packet, and it rewrites the packet in place:
				 * the trailer is removed and the length field put back, so the
				 * parser below sees exactly the octets that were signed. Nothing
				 * downstream knows this happened, which is the point - a control
				 * that every handler had to remember would be forgotten by one. */
				if (!pus_auth_ok(packet->data, &plen)) {
					printk("OBC: REJECTED an unauthenticated telecommand from node %u\n",
					       (unsigned int)csp_conn_src(conn));
					csp_buffer_free(packet);
					continue;
				}
#endif
				handle_space_packet(packet->data, plen, csp_conn_src(conn));
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

	if (!gpio_is_ready_dt(&fdir_flag)) {
		printk("OBC: FATAL FDIR indicator GPIO not ready\n");
		return -1;
	}
	/* Starts low. FDIR is active until something inhibits it, and nothing the ground can send
	 * legally does. */
	gpio_pin_configure_dt(&fdir_flag, GPIO_OUTPUT_INACTIVE);

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
