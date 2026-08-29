"""CCSDS Frame Error Control Field CRC.

CRC-16/IBM-3740: width 16, poly 0x1021, init 0xFFFF, refin/refout false, xorout 0x0000.
Source: CCSDS 132.0-B-3 s4.1.6.2.2. Do not call this "CRC-16-CCITT" - that name is ambiguous
across at least ten parameter sets and picking the wrong one still round-trips.
"""

POLY = 0x1021
INIT = 0xFFFF


def crc16_ccsds(data: bytes) -> int:
    """Return the 16-bit FECF over `data`.

    The FECF covers the frame from the first octet of the primary header through the last octet
    before the FECF itself. The Attached Sync Marker is NOT covered.
    """
    reg = INIT
    for byte in data:
        reg ^= byte << 8
        for _ in range(8):
            if reg & 0x8000:
                reg = ((reg << 1) ^ POLY) & 0xFFFF
            else:
                reg = (reg << 1) & 0xFFFF
    return reg
