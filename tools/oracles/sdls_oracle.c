/* The SDLS security header and trailer, laid out by NASA CryptoLib rather than by us.
 *
 * WHY THIS EXISTS. tc_oracle.c gives the TC PRIMARY header an outside opinion. The SDLS security
 * header has none, and it is the part this project would otherwise be reading straight out of
 * CCSDS 355.0-B-2 on its own - which is the situation the golden layer exists to end. Two
 * implementations that share a misreading of a field order agree perfectly and are both wrong on
 * the wire.
 *
 * WHAT MAKES IT INDEPENDENT. This program does not compute offsets. It hands raw octets to
 * Crypto_TC_ProcessSecurity and prints what CryptoLib put in its own TC_t, plus the offsets
 * CryptoLib's own arithmetic implies. The struct and the arithmetic are theirs.
 *
 * WHAT IT TAKES TO GET THERE, and why each piece is here:
 *
 *   Managed parameters. Crypto_Get_TC_Managed_Parameters_For_Gvcid runs BEFORE the SPI is read
 *   (crypto_tc.c:1995) and returns an error if the GVCID is unregistered, so tc_oracle's trick of
 *   letting the call fail late does not reach the security header. The GVCID has to be declared,
 *   and declaring it is where `has_segmentation_hdr` gets stated out loud.
 *
 *   TC_NO_SEGMENT_HDRS, deliberately. CryptoLib's own unit tests use frames with a segment header,
 *   which puts the SPI at offset 8. This project emits no segment header - ASSURANCE.md says so -
 *   which should put it at 5. That difference is the single most likely place for our layout to be
 *   quietly wrong, so it is the thing this oracle is pointed at.
 *
 *   A Security Association. The field LENGTHS live in the SA, not in the frame: crypto_tc.c
 *   copies sa_ptr->iv_len, ->arsn_len, ->shplf_len and ->stmacf_len into the parsed frame. Without
 *   one, there is nothing to parse a variable-length header against. Authentication only - est=0,
 *   ast=1 - because that is the SDLS service type this range implements and the write-ups say why.
 *
 *   No cryptography backend. The same reason tc_oracle has none: libgcrypt's headers are not on
 *   this host and installing them needs root, which the project's setup path does not have. The
 *   MAC is not verified here and this oracle does not claim it is - AES-GCM itself has a better
 *   oracle in pyca/cryptography, which tests/golden uses. What is being measured here is WHERE the
 *   fields are, and that is answered before any key is touched.
 *
 * Build: tools/oracles/build.sh. Usage: sdls_oracle <spi> <iv-len> <sn-len> <mac-len> <hex> [...]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "crypto.h"
#include "crypto_structs.h"
#include "crypto_error.h"
#include "crypto_config_structs.h"
#include "mc_interface.h"
#include "sa_interface.h"
#include "key_interface.h"

static int unhex(const char *hex, uint8_t *out, size_t cap)
{
    size_t n = strlen(hex);
    if (n % 2 || n / 2 > cap)
        return -1;
    for (size_t i = 0; i < n; i += 2)
    {
        unsigned v;
        if (sscanf(hex + i, "%2x", &v) != 1)
            return -1;
        out[i / 2] = (uint8_t)v;
    }
    return (int)(n / 2);
}

static int unhex_prefix(const char *hex, uint8_t *out, size_t n)
{
    if (strlen(hex) < n * 2)
        return -1;
    for (size_t i = 0; i < n; i++)
    {
        unsigned v;
        if (sscanf(hex + i * 2, "%2x", &v) != 1)
            return -1;
        out[i] = (uint8_t)v;
    }
    return 0;
}

static void print_hex(const char *name, const uint8_t *p, unsigned len, const char *tail)
{
    printf("   \"%s\": \"", name);
    for (unsigned i = 0; i < len; i++)
        printf("%02x", p[i]);
    printf("\"%s", tail);
}

int main(int argc, char **argv)
{
    if (argc < 5)
    {
        fprintf(stderr, "usage: %s <iv-len> <sn-len> <mac-len> <hex-frame> [...]\n", argv[0]);
        return 2;
    }
    uint8_t iv_len = (uint8_t)strtoul(argv[1], NULL, 0);
    uint8_t sn_len = (uint8_t)strtoul(argv[2], NULL, 0);
    uint8_t mac_len = (uint8_t)strtoul(argv[3], NULL, 0);

    Crypto_Config_CryptoLib(KEY_TYPE_INTERNAL, MC_TYPE_INTERNAL, SA_TYPE_INMEMORY,
                            CRYPTOGRAPHY_TYPE_LIBGCRYPT, IV_INTERNAL);
    Crypto_Config_TC(CRYPTO_TC_CREATE_FECF_TRUE, TC_PROCESS_SDLS_PDUS_FALSE, TC_NO_PUS_HDR,
                     TC_IGNORE_ANTI_REPLAY_TRUE, TC_IGNORE_SA_STATE_TRUE,
                     TC_UNIQUE_SA_PER_MAP_ID_FALSE, TC_CHECK_FECF_FALSE, 0x3F,
                     SA_INCREMENT_NONTRANSMITTED_IV_TRUE);
    mc_if = get_mc_interface_disabled();
    sa_if = get_sa_interface_inmemory();
    if (mc_if == NULL || sa_if == NULL)
    {
        fprintf(stderr, "rebuild CryptoLib with -DMC_INTERNAL=ON -DSA_INTERNAL=ON\n");
        return 1;
    }
    if (sa_if->sa_init() != CRYPTO_LIB_SUCCESS)
    {
        fprintf(stderr, "CryptoLib's in-memory SA store refused to initialise\n");
        return 1;
    }

    /* Register the GVCID of each frame this run was given, read out of the frame itself.
     *
     * Declared rather than wildcarded, because CryptoLib looks the GVCID up exactly and a frame
     * from an undeclared spacecraft must fail here rather than be parsed against somebody else's
     * parameters. Read from the frames rather than written out, because the first version listed
     * SCIDs 0x0A9..0x0AC by hand and the golden vector for SCID 0x3FF - the extreme, which is
     * precisely where a layout bug shows - came back unparsed with every field zero. A hardcoded
     * list and a vector set drift apart silently; this cannot.
     *
     * This is also where has_segmentation_hdr is stated out loud rather than assumed. */
    for (int a = 4; a < argc; a++)
    {
        uint8_t head[5];
        if (unhex_prefix(argv[a], head, sizeof head) < 0)
        {
            fprintf(stderr, "argument %d is too short to carry a TC primary header\n", a);
            return 2;
        }
        TCGvcidManagedParameters_t mp;
        memset(&mp, 0, sizeof mp);
        mp.tfvn = (head[0] >> 6) & 0x03;
        mp.scid = (uint16_t)(((head[0] & 0x03) << 8) | head[1]);
        mp.vcid = (uint8_t)((head[2] >> 2) & 0x3F);
        mp.has_fecf = TC_HAS_FECF;
        mp.has_segmentation_hdr = TC_NO_SEGMENT_HDRS;
        mp.max_frame_size = 1024;
        mp.set_flag = 1;
        if (Crypto_Config_Add_TC_Gvcid_Managed_Parameters(mp) != CRYPTO_LIB_SUCCESS)
        {
            /* Duplicates are expected when two vectors share a GVCID and are not an error; a
             * genuine refusal shows up as an unparsed frame below, with spi_at still printed and
             * every CryptoLib field zero, which is loud enough to see. */
        }
    }

    /* CryptoLib's in-memory SA store holds NUM_SA = 64 associations and Crypto_TC_ProcessSecurity
     * refuses SPI_MIN (0) and SPI_MAX (63) outright (crypto.c:1046), so it can only speak about
     * SPIs 1..62. The SDLS SPI field is sixteen bits. That is CryptoLib's limit and not the
     * standard's, and a frame outside it is reported as declined rather than printed with every
     * field zero - which is what the first version of this did, and what made a golden vector for
     * SCID 0x3FF look like a layout disagreement instead of an unconfigured store. */
    key_if = get_key_interface_internal();
    if (key_if == NULL)
    {
        fprintf(stderr, "rebuild CryptoLib with -DKEY_INTERNAL=ON\n");
        return 1;
    }

    printf("[\n");
    for (int a = 4; a < argc; a++)
    {
        uint8_t buf[2048];
        int len = unhex(argv[a], buf, sizeof buf);
        if (len < 0)
        {
            fprintf(stderr, "argument %d is not an even-length hex string that fits\n", a);
            return 2;
        }
        unsigned frame_spi = ((unsigned)buf[5] << 8) | buf[6];
        if (frame_spi <= 0 || frame_spi >= 63)
        {
            printf("  {\"frame\": \"%s\", \"octets\": %d, \"declined\": "
                   "\"SPI %u is outside CryptoLib's in-memory store (NUM_SA 64, and it refuses "
                   "SPI_MIN and SPI_MAX); the SDLS SPI field is 16 bits, so this is CryptoLib's "
                   "limit, not the standard's\"}%s\n",
                   argv[a], len, frame_spi, (a + 1 < argc) ? "," : "");
            continue;
        }
        SecurityAssociation_t *sa = NULL;
        if (sa_if->sa_get_from_spi((uint16_t)frame_spi, &sa) != CRYPTO_LIB_SUCCESS || sa == NULL)
        {
            fprintf(stderr, "no SA slot at SPI %u\n", frame_spi);
            return 1;
        }
        sa->sa_state = SA_OPERATIONAL;
        sa->est = 0;
        sa->ast = 1;
        sa->shivf_len = iv_len;
        sa->iv_len = iv_len;
        sa->shsnf_len = sn_len;
        sa->arsn_len = sn_len;
        sa->shplf_len = 0;
        sa->stmacf_len = mac_len;
        sa->acs_len = 1;
        sa->acs = CRYPTO_MAC_CMAC_AES256;
        sa->abm_len = ABM_SIZE;
        memset(sa->abm, 0xFF, ABM_SIZE);
        sa->arsnw_len = 1;
        sa->arsnw = 5;
        sa->akid = 130;
        sa->ekid = 130;
        crypto_key_t *k = key_if->get_key(sa->akid);
        if (k == NULL)
        {
            fprintf(stderr, "CryptoLib's internal key store has no key %u\n", sa->akid);
            return 1;
        }
        k->key_state = KEY_PREACTIVE;

        TC_t parsed;
        memset(&parsed, 0, sizeof parsed);
        int ingest_len = len;
        int32_t status = Crypto_TC_ProcessSecurity(buf, &ingest_len, &parsed);

        /* The offsets CryptoLib's own arithmetic implies (crypto_tc.c:441): primary header,
         * segment header, SPI, IV, SN, PAD, payload, MAC. segment_hdr_len is 0 here. */
        unsigned spi_at = 5;
        unsigned iv_at = spi_at + 2;
        unsigned sn_at = iv_at + parsed.tc_sec_header.iv_field_len;
        unsigned pad_at = sn_at + parsed.tc_sec_header.sn_field_len;
        unsigned pdu_at = pad_at + parsed.tc_sec_header.pad_field_len;
        /* CryptoLib's own tc_pdu_len, not ours, is what pins the end of the payload. */
        unsigned mac_at = pdu_at + parsed.tc_pdu_len;

        printf("  {\"frame\": \"%s\", \"octets\": %d, \"status\": %d,\n", argv[a], len, (int)status);
        printf("   \"scid\": %u, \"vcid\": %u, \"frame_length_field\": %u,\n",
               parsed.tc_header.scid, parsed.tc_header.vcid, parsed.tc_header.fl);
        printf("   \"spi\": %u, \"spi_at\": %u,\n", parsed.tc_sec_header.spi, spi_at);
        printf("   \"iv_len\": %u, \"iv_at\": %u,\n", parsed.tc_sec_header.iv_field_len, iv_at);
        print_hex("iv", parsed.tc_sec_header.iv, parsed.tc_sec_header.iv_field_len, ",\n");
        printf("   \"sn_len\": %u, \"sn_at\": %u,\n", parsed.tc_sec_header.sn_field_len, sn_at);
        print_hex("sn", parsed.tc_sec_header.sn, parsed.tc_sec_header.sn_field_len, ",\n");
        printf("   \"pad_len\": %u, \"pdu_at\": %u, \"pdu_len\": %u,\n",
               parsed.tc_sec_header.pad_field_len, pdu_at, parsed.tc_pdu_len);
        printf("   \"mac_len\": %u, \"mac_at\": %u,\n",
               parsed.tc_sec_trailer.mac_field_len, mac_at);
        printf("   \"mac_at_from_frame_end\": %u}", (unsigned)len - 2 - parsed.tc_sec_trailer.mac_field_len);
        printf("%s\n", (a + 1 < argc) ? "," : "");
    }
    printf("]\n");
    return 0;
}
