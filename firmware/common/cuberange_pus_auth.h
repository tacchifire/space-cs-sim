/* Authentication on the TELECOMMAND: the layout, and only the layout.
 *
 * A mission-defined trailer inside the Space Packet, so it travels with the packet through any
 * framing - a TC transfer frame on the space link, a CSP packet on the crosslink, a raw CAN
 * payload on the internal bus:
 *
 *     primary(6) | PUS TC secondary(5) | application data | SEQ(4) | MAC(16)
 *
 * and the Space Packet's own data-field length covers the trailer.
 *
 * THE LAYOUT IS MISSION-DEFINED AND HAS NO OUTSIDE ORACLE. ECSS-E-ST-70-41C defines no
 * authentication field for a TC packet, and CCSDS puts security at the transfer-frame layer -
 * which is exactly the layer this exists to stop depending on. tests/golden/pus_auth.json says
 * that in its `oracles` list rather than naming something that only resembles one. What IS
 * checked from outside is the primitive: AES-256-GCM, against libsodium and the NIST vectors.
 *
 * WHY NO CRYPTOGRAPHY IN HERE. Same reason as cuberange_sdls.h: this file is compiled for the
 * host by tests/native and for the spacecraft by Zephyr, and keeping the split pure is what lets
 * test_c_matches_python.py compare it against src/cuberange/proto/pus_auth.py field for field.
 * The caller verifies the MAC with whatever library it has.
 */
#ifndef CUBERANGE_PUS_AUTH_H
#define CUBERANGE_PUS_AUTH_H

#include <stddef.h>
#include <stdint.h>

#define CR_PUS_AUTH_SEQ_LEN   4
#define CR_PUS_AUTH_MAC_LEN   16
#define CR_PUS_AUTH_TRAILER   (CR_PUS_AUTH_SEQ_LEN + CR_PUS_AUTH_MAC_LEN)
#define CR_PUS_AUTH_NONCE_LEN 12
#define CR_PUS_AUTH_KEY_LEN   32

#define CR_PUS_AUTH_DIR_TC 1
#define CR_PUS_AUTH_DIR_TM 0

struct cr_pus_auth_parts {
	uint16_t apid;
	/* The counterparty: a TC's source id, or a TM's destination id. PUS puts them at different
	 * offsets - a TC secondary header is version/service/subtype/source(2), a TM's is
	 * version/service/subtype/counter(2)/destination(2) - so this is read from whichever the
	 * packet's type bit says it is. */
	uint16_t party_id;
	uint8_t direction;
	uint32_t seq;
	const uint8_t *mac;
	/* The authenticated region: packet[0 .. aad_len), which is everything through the sequence
	 * number. The MAC follows it and the packet ends there. */
	const uint8_t *aad;
	size_t aad_len;
	/* The packet WITHOUT the trailer is not produced here: rewriting its length field needs a
	 * mutable copy, and this function promises to touch nothing. `inner_len` is how long that
	 * packet will be, so a caller can size a buffer before deciding to trust anything. */
	size_t inner_len;
};

/* Split one authenticated TC Space Packet. Returns 0, or negative and touches nothing:
 *   -1  too short to hold a header, a secondary header and a trailer
 *   -2  the data-field length disagrees with the octet count
 * No cryptography happens here and nothing is trusted yet. */
int cr_pus_auth_split(const uint8_t *packet, size_t len, struct cr_pus_auth_parts *out);

/* The GCM nonce, derived rather than transmitted:
 *
 *     APID(2) | counterparty(2) | seq(4) | direction(1) | zeros(3)
 *
 * Writes CR_PUS_AUTH_NONCE_LEN octets. Every field is load-bearing, because GCM punishes nonce
 * reuse by leaking the authentication subkey rather than merely a plaintext:
 *
 *   the counterparty, because several ground stations share the spacecraft's APID;
 *   the sequence, because that is the number the anti-replay check refuses to see twice;
 *   THE DIRECTION, added when telemetry was signed - a telecommand from station 0x0042 with
 *   sequence 5 and a report TO station 0x0042 with sequence 5 are different packets that
 *   produced the same nonce under the same key. One octet separates them.
 */
void cr_pus_auth_nonce(uint16_t apid, uint16_t party_id, uint32_t seq, uint8_t direction,
		       uint8_t *out);

/* TC or TM, from the Space Packet primary header's type bit (CCSDS 133.0-B 4.1.2.3.2). */
uint8_t cr_pus_auth_direction(const uint8_t *packet);

/* Make room for a trailer on a finished Space Packet and fill in everything except the MAC.
 *
 * `packet` must have CR_PUS_AUTH_TRAILER octets of space after `len`. The data-field length is
 * rewritten to cover the trailer BEFORE the caller MACs anything, so the number an attacker would
 * change to strip the trailer is itself authenticated - the same ordering src/cuberange/proto/
 * pus_auth.py uses, and test_c_matches_python.py compares the two.
 *
 * Returns the new total length. `out->aad`/`out->aad_len` is what to MAC; write the tag at
 * `packet + out->aad_len`. Nothing here computes it. */
size_t cr_pus_auth_prepare(uint8_t *packet, size_t len, uint32_t seq,
			   struct cr_pus_auth_parts *out);

/* Rewrite `packet`'s data-field length so it describes the packet with the trailer removed.
 * Call only after the MAC has verified; `len` is the FULL length including the trailer. */
void cr_pus_auth_strip(uint8_t *packet, size_t len);

#endif /* CUBERANGE_PUS_AUTH_H */
