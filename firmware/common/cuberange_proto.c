/* SPDX-License-Identifier: Apache-2.0 */
#include <string.h>

#include "cuberange_proto.h"

static const uint8_t CR_ASM[4] = {CR_ASM_0, CR_ASM_1, CR_ASM_2, CR_ASM_3};

uint16_t cr_crc16(const uint8_t *data, size_t len)
{
	uint16_t reg = 0xFFFF;

	for (size_t i = 0; i < len; i++) {
		reg ^= (uint16_t)((uint16_t)data[i] << 8);
		for (int bit = 0; bit < 8; bit++) {
			reg = (reg & 0x8000) ? (uint16_t)((uint16_t)(reg << 1) ^ 0x1021)
					     : (uint16_t)(reg << 1);
		}
	}
	return reg;
}

static void append_fecf(uint8_t *frame, size_t body_len)
{
	uint16_t fecf = cr_crc16(frame, body_len);

	frame[body_len] = (uint8_t)(fecf >> 8);
	frame[body_len + 1] = (uint8_t)(fecf & 0xFF);
}

int cr_encode_tc_frame(uint8_t *out, size_t out_cap,
		       const uint8_t *payload, size_t len, uint8_t seq)
{
	size_t total = CR_TC_HEADER_LEN + len + CR_FECF_LEN;

	if (total > out_cap || total > CR_MAX_FRAME_LEN) {
		return -1;
	}
	uint16_t length_field = (uint16_t)(total - 1);

	out[0] = (uint8_t)((CR_SCID >> 8) & 0x03);
	out[1] = (uint8_t)(CR_SCID & 0xFF);
	out[2] = (uint8_t)(((CR_VCID & 0x3F) << 2) | ((length_field >> 8) & 0x03));
	out[3] = (uint8_t)(length_field & 0xFF);
	out[4] = seq;
	if (len > 0) {
		memcpy(out + CR_TC_HEADER_LEN, payload, len);
	}
	append_fecf(out, CR_TC_HEADER_LEN + len);
	return (int)total;
}

int cr_encode_tm_frame(uint8_t *out, size_t out_cap,
		       const uint8_t *payload, size_t len, uint8_t mc, uint8_t vc)
{
	size_t total = CR_TM_HEADER_LEN + len + CR_FECF_LEN;

	if (total > out_cap || total > CR_MAX_FRAME_LEN) {
		return -1;
	}
	uint16_t word0 = (uint16_t)(((CR_SCID & 0x3FF) << 4) | ((CR_VCID & 0x7) << 1));

	out[0] = (uint8_t)(word0 >> 8);
	out[1] = (uint8_t)(word0 & 0xFF);
	out[2] = mc;
	out[3] = vc;
	out[4] = 0;                     /* data field status: first header pointer = 0 */
	out[5] = 0;
	if (len > 0) {
		memcpy(out + CR_TM_HEADER_LEN, payload, len);
	}
	append_fecf(out, CR_TM_HEADER_LEN + len);
	return (int)total;
}

uint8_t cr_tc_frame_vcid(const uint8_t *frame, size_t len)
{
	if (len < CR_TC_HEADER_LEN) {
		return 0xFF;
	}
	return (uint8_t)((frame[2] >> 2) & 0x3F);
}

int cr_decode_tc_frame(const uint8_t *frame, size_t len, uint8_t *seq,
		       const uint8_t **payload, size_t *payload_len)
{
	if (len < CR_TC_HEADER_LEN + CR_FECF_LEN || cr_crc16(frame, len) != 0x0000) {
		return -1;
	}
	size_t declared = (size_t)(((frame[2] & 0x03) << 8) | frame[3]) + 1;

	if (declared != len) {
		return -1;
	}
	*seq = frame[4];
	*payload = frame + CR_TC_HEADER_LEN;
	*payload_len = len - CR_TC_HEADER_LEN - CR_FECF_LEN;
	return 0;
}

int cr_decode_tm_frame(const uint8_t *frame, size_t len, uint8_t *mc, uint8_t *vc,
		       const uint8_t **payload, size_t *payload_len)
{
	if (len < CR_TM_HEADER_LEN + CR_FECF_LEN || cr_crc16(frame, len) != 0x0000) {
		return -1;
	}
	*mc = frame[2];
	*vc = frame[3];
	*payload = frame + CR_TM_HEADER_LEN;
	*payload_len = len - CR_TM_HEADER_LEN - CR_FECF_LEN;
	return 0;
}

size_t cr_wrap(uint8_t *out, size_t out_cap, const uint8_t *frame, size_t len)
{
	size_t total = sizeof(CR_ASM) + 2 + len;

	if (total > out_cap) {
		return 0;
	}
	memcpy(out, CR_ASM, sizeof(CR_ASM));
	out[4] = (uint8_t)(len >> 8);
	out[5] = (uint8_t)(len & 0xFF);
	memcpy(out + 6, frame, len);
	return total;
}

void cr_deframer_init(cr_deframer_t *d)
{
	d->used = 0;
}

int cr_deframer_feed(cr_deframer_t *d, const uint8_t *chunk, size_t len,
		     cr_frame_cb cb, void *ctx)
{
	int emitted = 0;

	for (size_t i = 0; i < len; i++) {
		if (d->used < sizeof(d->buf)) {
			d->buf[d->used++] = chunk[i];
		} else {
			/* Overflow can only mean we are tracking garbage. Drop the oldest octet so
			 * the next real ASM can still be found. */
			memmove(d->buf, d->buf + 1, sizeof(d->buf) - 1);
			d->buf[sizeof(d->buf) - 1] = chunk[i];
		}

		for (;;) {
			size_t start = 0;
			int found = 0;

			while (start + sizeof(CR_ASM) <= d->used) {
				if (memcmp(d->buf + start, CR_ASM, sizeof(CR_ASM)) == 0) {
					found = 1;
					break;
				}
				start++;
			}
			if (!found) {
				/* Retain only what could still be a partial ASM. */
				size_t keep = (d->used < sizeof(CR_ASM) - 1)
						? d->used : sizeof(CR_ASM) - 1;

				memmove(d->buf, d->buf + d->used - keep, keep);
				d->used = keep;
				break;
			}
			if (start > 0) {
				memmove(d->buf, d->buf + start, d->used - start);
				d->used -= start;
			}
			if (d->used < sizeof(CR_ASM) + 2) {
				break;
			}
			size_t flen = ((size_t)d->buf[4] << 8) | d->buf[5];

			if (flen == 0 || flen > CR_MAX_FRAME_LEN) {
				memmove(d->buf, d->buf + sizeof(CR_ASM),
					d->used - sizeof(CR_ASM));
				d->used -= sizeof(CR_ASM);
				continue;
			}
			size_t end = sizeof(CR_ASM) + 2 + flen;

			if (d->used < end) {
				break;
			}
			cb(d->buf + sizeof(CR_ASM) + 2, flen, ctx);
			emitted++;
			memmove(d->buf, d->buf + end, d->used - end);
			d->used -= end;
		}
	}
	return emitted;
}
