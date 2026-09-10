/* TC transfer frame primary header, parsed by NASA CryptoLib rather than by us.
 *
 * WHY THIS EXISTS. tests/golden/crc.json gives the FECF four independent opinions, and
 * space_packet.json gives the Space Packet header two. The TM and TC TRANSFER FRAME headers had
 * none: their field layout rested entirely on this project's reading of CCSDS 232.0-B-4 and
 * 132.0-B-3, and ASSURANCE.md said so. Two implementations that share a misreading agree
 * perfectly and are both wrong on the wire, which is the whole reason the golden layer exists.
 *
 * WHAT MAKES IT INDEPENDENT. This program does not reimplement the layout. It hands raw octets to
 * Crypto_TC_ProcessSecurity and prints what CryptoLib put in its own TC_t. The struct is
 * CryptoLib's, compiled from CryptoLib's header by the same compiler CryptoLib uses, so the bit
 * widths and the order are theirs. Transcribing their shift expressions into this file would have
 * proved nothing.
 *
 * WHAT THE STATUS MEANS. The call is expected to fail: parsing runs first, and the later Security
 * Association lookup has nothing to find. The primary header is already populated by then. One
 * status IS interesting - CRYPTO_LIB_ERR_TC_FRAME_LENGTH_MISMATCH (-27) means CryptoLib disagreed
 * with our length field, because it checks `fl + 1 == len`. That is an outside confirmation of the
 * "total octets minus one" convention, which is the single easiest field in the header to get
 * wrong, so it is reported rather than swallowed.
 *
 * Build: tools/oracles/build.sh. Usage: tc_oracle <hex-frame> [<hex-frame> ...]
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "crypto.h"
#include "crypto_structs.h"
#include "crypto_error.h"
#include "mc_interface.h"
#include "sa_interface.h"

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

int main(int argc, char **argv)
{
    if (argc < 2)
    {
        fprintf(stderr, "usage: %s <hex-frame> [<hex-frame> ...]\n", argv[0]);
        return 2;
    }

    /* Deliberately NOT Crypto_Init_TC_Unit_Test().
     *
     * That helper asks for CRYPTOGRAPHY_TYPE_LIBGCRYPT, so Crypto_Init() fails on a host without
     * libgcrypt headers - which is this one, and installing them needs root, which the project's
     * own setup path does not have. A cryptography backend is irrelevant to reading a primary
     * header, so configure exactly what the parse path checks and nothing else:
     *
     *   Crypto_TC_Process_Sanity_Check refuses unless crypto_config_global.init_status and
     *   crypto_config_tc.init_status are set and mc_if and sa_if are non-NULL.
     *
     * The two Config calls set the statuses; the interfaces are CryptoLib's own, fetched through
     * its own getters. Nothing here substitutes our behaviour for theirs - the parse below is
     * still entirely CryptoLib's code.
     *
     * vcid_bitmask is 0x3F because the TC VCID field is six bits (CCSDS 232.0-B-4 4.1.2.4). Get it
     * wrong and CryptoLib masks the VCID down and the oracle silently agrees with a wrong answer,
     * so a vector below deliberately uses VCID 63 to keep that honest.
     */
    Crypto_Config_CryptoLib(KEY_TYPE_INTERNAL, MC_TYPE_INTERNAL, SA_TYPE_INMEMORY,
                            CRYPTOGRAPHY_TYPE_LIBGCRYPT, IV_INTERNAL);
    Crypto_Config_TC(CRYPTO_TC_CREATE_FECF_TRUE, TC_PROCESS_SDLS_PDUS_FALSE, TC_NO_PUS_HDR,
                     TC_IGNORE_ANTI_REPLAY_TRUE, TC_IGNORE_SA_STATE_TRUE,
                     TC_UNIQUE_SA_PER_MAP_ID_FALSE, TC_CHECK_FECF_FALSE, 0x3F,
                     SA_INCREMENT_NONTRANSMITTED_IV_TRUE);
    /* The DISABLED monitoring interface, not the internal one. mc_log in the internal template
     * fprintf's to a FILE* that Crypto_Init opens, and calling it with that handle still NULL
     * segfaults inside libc - which is what happened, on the perfectly ordinary path where a frame
     * has no managed parameters registered. Logging is not part of what is being measured. */
    mc_if = get_mc_interface_disabled();
    sa_if = get_sa_interface_inmemory();
    if (mc_if == NULL || sa_if == NULL)
    {
        fprintf(stderr, "CryptoLib's internal MC or SA interface is unavailable; rebuild with "
                        "-DMC_INTERNAL=ON -DSA_INTERNAL=ON\n");
        return 1;
    }
    /* The in-memory SA store is walked after the header is parsed. Crypto_Init() would call this;
     * without it the lookup dereferences an empty table and the process dies before printing. */
    if (sa_if->sa_init() != CRYPTO_LIB_SUCCESS)
    {
        fprintf(stderr, "CryptoLib's in-memory SA store refused to initialise\n");
        return 1;
    }

    printf("[\n");
    for (int a = 1; a < argc; a++)
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

        printf("  {\"frame\": \"%s\", \"octets\": %d, \"status\": %d,\n", argv[a], len, (int)status);
        printf("   \"tfvn\": %u, \"bypass\": %u, \"cc\": %u, \"spare\": %u,\n",
               parsed.tc_header.tfvn, parsed.tc_header.bypass,
               parsed.tc_header.cc, parsed.tc_header.spare);
        printf("   \"scid\": %u, \"vcid\": %u, \"frame_length_field\": %u, \"fsn\": %u,\n",
               parsed.tc_header.scid, parsed.tc_header.vcid,
               parsed.tc_header.fl, parsed.tc_header.fsn);
        /* The length agreement, stated rather than implied. */
        printf("   \"length_field_plus_one_equals_octets\": %s}%s\n",
               (parsed.tc_header.fl + 1 == len) ? "true" : "false",
               (a + 1 < argc) ? "," : "");
    }
    printf("]\n");
    return 0;
}
