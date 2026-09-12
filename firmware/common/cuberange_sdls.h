/* SDLS on the TC transfer frame: the LAYOUT, and only the layout.
 *
 * CCSDS 355.0-B-2 authentication service, as this range emits it - no segment header, no
 * encryption:
 *
 *     primary(5) | SPI(2) | IV(12) | SN(4) | PDU | MAC(16) | FECF(2)
 *
 * Those offsets are not this project's reading of the standard. `tools/oracles/sdls_oracle.c`
 * hands NASA CryptoLib frames it did not produce and prints where CryptoLib says the fields are;
 * tests/golden/sdls.json holds the answers and `test_golden_sdls.py` checks them.
 *
 * WHY NO CRYPTOGRAPHY IN HERE. This file is compiled for the host by tests/native and for the
 * spacecraft by Zephyr, and the two have different cryptographic libraries available - mbedTLS on
 * one, OpenSSL on the other. Keeping the split pure means `test_c_matches_python.py` can compare
 * it against src/cuberange/proto/sdls.py field for field, which is what catches a layout that
 * drifted. The MAC is verified by the caller, with whatever it has.
 *
 * The field lengths are PARAMETERS, not constants, and that is the point of a security header:
 * they come from the Security Association. A receiver using the wrong ones parses a different
 * frame of the same total length, which is why cr_sdls_split takes them and checks them.
 */
#ifndef CUBERANGE_SDLS_H
#define CUBERANGE_SDLS_H

#include <stddef.h>
#include <stdint.h>

#define CR_SDLS_SPI_LEN 2

/* What this range's one Security Association uses. Named so the firmware and the tests agree, and
 * passed explicitly anyway so nothing here assumes them. */
#define CR_SDLS_IV_LEN  12
#define CR_SDLS_SN_LEN  4
#define CR_SDLS_MAC_LEN 16
#define CR_SDLS_KEY_LEN 32

struct cr_sdls_parts {
	uint16_t spi;
	const uint8_t *iv;
	size_t iv_len;
	uint32_t seq_num;         /* only valid when sn_len <= 4 */
	const uint8_t *sn;
	size_t sn_len;
	const uint8_t *payload;
	size_t payload_len;
	const uint8_t *mac;
	size_t mac_len;
	/* The authenticated portion: frame[0 .. aad_len). Everything through the payload, and NOT
	 * the FECF - that covers the finished frame including the MAC. */
	const uint8_t *aad;
	size_t aad_len;
};

/* Split one authenticated TC frame. Returns 0, or negative and touches nothing:
 *   -1  too short to hold the fields these lengths describe
 *   -2  the length field disagrees with the octet count
 *   -3  FECF residue is not zero
 *   -4  the lengths are not ones this build supports
 * No cryptography happens here and nothing is trusted yet. */
int cr_sdls_split(const uint8_t *frame, size_t len, size_t iv_len, size_t sn_len,
		  size_t mac_len, struct cr_sdls_parts *out);

/* Where each field starts, given the lengths. Exposed so a test can assert the layout without
 * building a frame, and so the firmware can log an offset without recomputing it. */
size_t cr_sdls_spi_at(void);
size_t cr_sdls_iv_at(void);
size_t cr_sdls_sn_at(size_t iv_len);
size_t cr_sdls_pdu_at(size_t iv_len, size_t sn_len);

#endif /* CUBERANGE_SDLS_H */
