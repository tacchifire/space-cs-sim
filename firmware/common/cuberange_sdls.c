#include "cuberange_sdls.h"
#include "cuberange_proto.h"

#include <string.h>

/* The TC primary header is five octets and the FECF two; both are cuberange_proto's business and
 * both are restated here as names rather than numbers, because a 5 in an offset expression is the
 * hardest kind of constant to find later. */
#define TC_HEADER_LEN 5
#define FECF_LEN      2

size_t cr_sdls_spi_at(void)
{
	return TC_HEADER_LEN;
}

size_t cr_sdls_iv_at(void)
{
	return TC_HEADER_LEN + CR_SDLS_SPI_LEN;
}

size_t cr_sdls_sn_at(size_t iv_len)
{
	return cr_sdls_iv_at() + iv_len;
}

size_t cr_sdls_pdu_at(size_t iv_len, size_t sn_len)
{
	return cr_sdls_sn_at(iv_len) + sn_len;
}

int cr_sdls_split(const uint8_t *frame, size_t len, size_t iv_len, size_t sn_len,
		  size_t mac_len, struct cr_sdls_parts *out)
{
	if (frame == NULL || out == NULL) {
		return -1;
	}
	/* Refused rather than handled. A four-octet sequence number is what this range's SA uses
	 * and what seq_num below can hold; a longer one is a different profile, and quietly
	 * truncating it would produce an anti-replay counter that wraps where nobody expects. */
	if (iv_len == 0 || iv_len > 16 || sn_len > 4 || mac_len == 0 || mac_len > 16) {
		return -4;
	}

	const size_t pdu_at = cr_sdls_pdu_at(iv_len, sn_len);
	const size_t minimum = pdu_at + mac_len + FECF_LEN;

	if (len < minimum) {
		return -1;
	}
	/* The length field, before anything is read past it. CCSDS 232.0-B-4: total octets minus
	 * one, in the low two bits of octet 2 and all of octet 3. */
	const size_t declared = (size_t)(((frame[2] & 0x03) << 8) | frame[3]) + 1u;

	if (declared != len) {
		return -2;
	}
	if (cr_crc16(frame, len) != 0x0000) {
		return -3;
	}

	const size_t mac_at = len - FECF_LEN - mac_len;

	if (mac_at < pdu_at) {
		return -1;
	}

	memset(out, 0, sizeof(*out));
	out->spi = (uint16_t)(((uint16_t)frame[cr_sdls_spi_at()] << 8) |
			      frame[cr_sdls_spi_at() + 1]);
	out->iv = frame + cr_sdls_iv_at();
	out->iv_len = iv_len;
	out->sn = frame + cr_sdls_sn_at(iv_len);
	out->sn_len = sn_len;
	out->seq_num = 0;
	for (size_t i = 0; i < sn_len; i++) {
		out->seq_num = (out->seq_num << 8) | out->sn[i];
	}
	out->payload = frame + pdu_at;
	out->payload_len = mac_at - pdu_at;
	out->mac = frame + mac_at;
	out->mac_len = mac_len;
	out->aad = frame;
	out->aad_len = mac_at;
	return 0;
}
