#include "cuberange_pus_auth.h"

#include <string.h>

#define SP_HEADER_LEN 6
#define PUS_TC_SEC_LEN 5

void cr_pus_auth_nonce(uint16_t apid, uint16_t party_id, uint32_t seq, uint8_t direction,
		       uint8_t *out)
{
	memset(out, 0, CR_PUS_AUTH_NONCE_LEN);
	out[0] = (uint8_t)(apid >> 8);
	out[1] = (uint8_t)apid;
	out[2] = (uint8_t)(party_id >> 8);
	out[3] = (uint8_t)party_id;
	out[4] = (uint8_t)(seq >> 24);
	out[5] = (uint8_t)(seq >> 16);
	out[6] = (uint8_t)(seq >> 8);
	out[7] = (uint8_t)seq;
	out[8] = direction;
}

uint8_t cr_pus_auth_direction(const uint8_t *packet)
{
	return ((packet[0] >> 4) & 0x01u) ? CR_PUS_AUTH_DIR_TC : CR_PUS_AUTH_DIR_TM;
}

/* Where the counterparty id sits, after the primary header, in each direction. */
static size_t party_offset(uint8_t direction)
{
	return (direction == CR_PUS_AUTH_DIR_TC) ? 3u : 5u;
}

int cr_pus_auth_split(const uint8_t *packet, size_t len, struct cr_pus_auth_parts *out)
{
	if (packet == NULL || out == NULL) {
		return -1;
	}
	const size_t minimum = SP_HEADER_LEN + PUS_TC_SEC_LEN + CR_PUS_AUTH_TRAILER;

	if (len < minimum) {
		return -1;
	}
	/* The declared length, before anything is read past it. CCSDS 133.0-B: the data field length
	 * is the number of octets after the primary header, minus one. */
	const size_t declared = (size_t)(((uint16_t)packet[4] << 8) | packet[5]) + 1u;

	if (declared != len - SP_HEADER_LEN) {
		return -2;
	}

	const size_t mac_at = len - CR_PUS_AUTH_MAC_LEN;
	const size_t seq_at = mac_at - CR_PUS_AUTH_SEQ_LEN;

	memset(out, 0, sizeof(*out));
	out->apid = (uint16_t)((((uint16_t)packet[0] << 8) | packet[1]) & 0x07FF);
	out->direction = cr_pus_auth_direction(packet);

	const size_t party_at = SP_HEADER_LEN + party_offset(out->direction);

	out->party_id = (uint16_t)(((uint16_t)packet[party_at] << 8) | packet[party_at + 1]);
	out->seq = ((uint32_t)packet[seq_at] << 24) | ((uint32_t)packet[seq_at + 1] << 16) |
		   ((uint32_t)packet[seq_at + 2] << 8) | packet[seq_at + 3];
	out->mac = packet + mac_at;
	out->aad = packet;
	out->aad_len = mac_at;
	out->inner_len = seq_at;
	return 0;
}

size_t cr_pus_auth_prepare(uint8_t *packet, size_t len, uint32_t seq,
			   struct cr_pus_auth_parts *out)
{
	const size_t total = len + CR_PUS_AUTH_TRAILER;
	const uint16_t declared = (uint16_t)(total - SP_HEADER_LEN - 1u);

	packet[4] = (uint8_t)(declared >> 8);
	packet[5] = (uint8_t)declared;

	uint8_t *sn = packet + len;

	sn[0] = (uint8_t)(seq >> 24);
	sn[1] = (uint8_t)(seq >> 16);
	sn[2] = (uint8_t)(seq >> 8);
	sn[3] = (uint8_t)seq;

	memset(out, 0, sizeof(*out));
	out->apid = (uint16_t)((((uint16_t)packet[0] << 8) | packet[1]) & 0x07FF);
	out->direction = cr_pus_auth_direction(packet);

	const size_t party_at = SP_HEADER_LEN + party_offset(out->direction);

	out->party_id = (uint16_t)(((uint16_t)packet[party_at] << 8) | packet[party_at + 1]);
	out->seq = seq;
	out->aad = packet;
	out->aad_len = len + CR_PUS_AUTH_SEQ_LEN;
	out->mac = packet + out->aad_len;
	out->inner_len = len;
	return total;
}

void cr_pus_auth_strip(uint8_t *packet, size_t len)
{
	const size_t inner = len - CR_PUS_AUTH_TRAILER;
	const uint16_t declared = (uint16_t)(inner - SP_HEADER_LEN - 1u);

	packet[4] = (uint8_t)(declared >> 8);
	packet[5] = (uint8_t)declared;
}
