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

#endif /* CUBERANGE_KEYS_H */
