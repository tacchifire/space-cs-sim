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

static void print_hex(const char *name, const uint8_t *p, unsigned len, const char *tail)
{
    printf("   \"%s\": \"", name);
    for (unsigned i = 0; i < len; i++)
        printf("%02x", p[i]);
    printf("\"%s", tail);
}

int main(int argc, char **argv)
{
    if (argc < 6)
    {
        fprintf(stderr, "usage: %s <spi> <iv-len> <sn-len> <mac-len> <hex-frame> [...]\n", argv[0]);
        return 2;
    }
    uint16_t spi = (uint16_t)strtoul(argv[1], NULL, 0);
    uint8_t iv_len = (uint8_t)strtoul(argv[2], NULL, 0);
    uint8_t sn_len = (uint8_t)strtoul(argv[3], NULL, 0);
    uint8_t mac_len = (uint8_t)strtoul(argv[4], NULL, 0);

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

    /* Every SCID and VCID this range uses. Declared rather than wildcarded: CryptoLib looks the
     * GVCID up exactly, and a frame from an undeclared spacecraft must fail here rather than be
     * parsed against somebody else's parameters. */
    for (uint16_t scid = 0x0A9; scid <= 0x0AC; scid++)
    {
        for (uint8_t vcid = 0; vcid < 4; vcid++)
        {
            TCGvcidManagedParameters_t mp;
            memset(&mp, 0, sizeof mp);
            mp.tfvn = 0;
            mp.scid = scid;
            mp.vcid = vcid;
            mp.has_fecf = TC_HAS_FECF;
            mp.has_segmentation_hdr = TC_NO_SEGMENT_HDRS;
            mp.max_frame_size = 1024;
            mp.set_flag = 1;
            if (Crypto_Config_Add_TC_Gvcid_Managed_Parameters(mp) != CRYPTO_LIB_SUCCESS)
            {
                fprintf(stderr, "CryptoLib refused managed parameters for SCID 0x%03X VC %u\n",
                        scid, vcid);
                return 1;
            }
        }
    }

    SecurityAssociation_t *sa = NULL;
    if (sa_if->sa_get_from_spi(spi, &sa) != CRYPTO_LIB_SUCCESS || sa == NULL)
    {
        fprintf(stderr, "no SA slot at SPI %u in CryptoLib's in-memory store\n", spi);
        return 1;
    }
    sa->sa_state = SA_OPERATIONAL;
    sa->est = 0;                        /* no encryption */
    sa->ast = 1;                        /* authentication only */
    sa->shivf_len = iv_len;
    sa->iv_len = iv_len;
    sa->shsnf_len = sn_len;
    sa->arsn_len = sn_len;
    sa->shplf_len = 0;
    sa->stmacf_len = mac_len;
    sa->acs_len = 1;
    sa->acs = CRYPTO_MAC_CMAC_AES256;   /* the MAC is not verified here; see the header comment */
    sa->abm_len = ABM_SIZE;
    memset(sa->abm, 0xFF, ABM_SIZE);
    sa->arsnw_len = 1;
    sa->arsnw = 5;
    /* A key that EXISTS and is not ACTIVE, on purpose. Two hazards had to be steered between:
     *
     *   A key id the store does not hold segfaults CryptoLib. Crypto_TC_Get_Keys detects
     *   `*akp == NULL`, sets CRYPTO_LIB_ERR_KEY_ID_ERROR, and then the NEXT statement evaluates
     *   `(*akp)->key_state` - the `&&` short-circuits on the right operand, not the left, so the
     *   NULL is dereferenced whatever the status says (crypto_tc.c:1785). Measured under gdb.
     *
     *   A key that is active carries the parse on into Crypto_TC_Do_Decrypt, which calls through
     *   `cryptography_if`. No cryptography backend is compiled in here - libgcrypt's headers need
     *   root - so that pointer is NULL too.
     *
     *   Between them: an existing key in a non-ACTIVE state returns CRYPTO_LIB_ERR_KEY_STATE_INVALID
     *   cleanly, at a point where the security header is fully parsed and Prep_AAD has already set
     *   tc_pdu_len. Everything this oracle measures is populated; the MAC bytes are not, because
     *   Do_Decrypt copies those, and this oracle does not print them.
     *
     * Key 130 is one the internal store holds; the state is what stops the parse. */
    sa->akid = 130;
    sa->ekid = 130;

    key_if = get_key_interface_internal();
    if (key_if == NULL)
    {
        fprintf(stderr, "rebuild CryptoLib with -DKEY_INTERNAL=ON\n");
        return 1;
    }
    crypto_key_t *k = key_if->get_key(sa->akid);
    if (k == NULL)
    {
        fprintf(stderr, "CryptoLib's internal key store has no key %u\n", sa->akid);
        return 1;
    }
    k->key_state = KEY_PREACTIVE;

    printf("[\n");
    for (int a = 5; a < argc; a++)
    {
        uint8_t buf[2048];
        int len = unhex(argv[a], buf, sizeof buf);
        if (len < 0)
        {
            fprintf(stderr, "argument %d is not an even-length hex string that fits\n", a);
            return 2;
        }
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
