/* The range's SDLS key, mirrored from src/cuberange/keys.py.
 *
 * Duplicated rather than shared because CMake and Python cannot read one source, which is the
 * same reason firmware/common/identity.cmake duplicates the addresses. tests/pytest/test_keys.py
 * parses both files and fails if they disagree, so a divergence is a test failure rather than a
 * spacecraft that rejects everything the ground sends with no explanation either side.
 *
 * This is NOT key management. It is a 32-octet constant in a public repository, and keys.py says
 * at length why that is the honest shape for this range rather than a shortcut. The octets spell
 * "cuberange-sdls-test-key-not-real" in ASCII on purpose: a key that looks like a key invites
 * somebody to wonder whether it is one.
 */
#ifndef CUBERANGE_KEYS_H
#define CUBERANGE_KEYS_H

#include <stdint.h>

#define CR_SDLS_SPI 9

static const uint8_t cr_sdls_key[32] = {
	0x63, 0x75, 0x62, 0x65, 0x72, 0x61, 0x6E, 0x67,
	0x65, 0x2D, 0x73, 0x64, 0x6C, 0x73, 0x2D, 0x74,
	0x65, 0x73, 0x74, 0x2D, 0x6B, 0x65, 0x79, 0x2D,
	0x6E, 0x6F, 0x74, 0x2D, 0x72, 0x65, 0x61, 0x6C,
};

/* The second Security Association, mirrored from keys.py the same way. CCSDS 355.0-B-2 gives an
 * SA a key, a mode, a sequence number and a STATE; retiring a key is deactivating its SA, and a
 * rotation that only activates the new one has retired nothing. EX-S03.
 */
#define CR_SDLS_SPI_ROTATED 10

static const uint8_t cr_sdls_key_rotated[32] = {
	0x63, 0x75, 0x62, 0x65, 0x72, 0x61, 0x6E, 0x67,
	0x65, 0x2D, 0x73, 0x64, 0x6C, 0x73, 0x2D, 0x72,
	0x6F, 0x74, 0x61, 0x74, 0x65, 0x64, 0x2D, 0x6B,
	0x65, 0x79, 0x2D, 0x6E, 0x6F, 0x74, 0x72, 0x6C,
};

/* A THIRD association, belonging to a DIFFERENT party - a partner or cross-support station that
 * holds its own SA on the same space link. This is what makes "a valid MAC" and "the operator" two
 * different things, and it is the whole of EX-S04: SA management by telecommand (CCSDS 355.1
 * Extended Procedures, the STOP_SA directive) where a frame that authenticates under SPI 11 must
 * NOT be allowed to retire the operator's SPI 9. Mirrored from keys.py; test_keys.py compares them.
 */
#define CR_SDLS_SPI_PARTNER 11

static const uint8_t cr_sdls_key_partner[32] = {
	0x63, 0x75, 0x62, 0x65, 0x72, 0x61, 0x6E, 0x67,
	0x65, 0x2D, 0x73, 0x64, 0x6C, 0x73, 0x2D, 0x70,
	0x61, 0x72, 0x74, 0x6E, 0x65, 0x72, 0x2D, 0x6E,
	0x6F, 0x74, 0x2D, 0x72, 0x65, 0x61, 0x6C, 0x21,
};

/* An SA's owner: the party it belongs to. EX-S04's authorisation check is exactly an equality of
 * these - a STOP_SA directive may retire an SA only when the frame carrying it authenticated under
 * an SA with the SAME owner. The values are arbitrary tags; only equality is compared. Mirrored
 * from keys.py (SA_OWNER_OPERATOR / SA_OWNER_PARTNER). */
#define CR_SDLS_OWNER_OPERATOR 1   /* SPI 9 and SPI 10 */
#define CR_SDLS_OWNER_PARTNER  2   /* SPI 11 */

#endif /* CUBERANGE_KEYS_H */
