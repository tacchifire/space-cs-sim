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

struct cr_pus_auth_parts {
	uint16_t apid;
	uint16_t source_id;
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

/* The GCM nonce, derived rather than transmitted: APID(2) | source(2) | seq(4) | zeros(4).
 *
 * Writes CR_PUS_AUTH_NONCE_LEN octets. Within one key the nonce repeats only if a source id
 * reuses a sequence number - which is the same event the anti-replay check refuses, so the two
 * properties are one property. GCM punishes nonce reuse by leaking the authentication subkey, not
 * merely a plaintext, which is why that is worth saying twice. */
void cr_pus_auth_nonce(uint16_t apid, uint16_t source_id, uint32_t seq, uint8_t *out);

/* Rewrite `packet`'s data-field length so it describes the packet with the trailer removed.
 * Call only after the MAC has verified; `len` is the FULL length including the trailer. */
void cr_pus_auth_strip(uint8_t *packet, size_t len);

#endif /* CUBERANGE_PUS_AUTH_H */
