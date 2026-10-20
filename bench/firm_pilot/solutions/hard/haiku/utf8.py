def utf8_decode(b: bytes) -> str:
    """Decode UTF-8 bytes to str without built-in decoders.

    Follows the WHATWG Encoding Standard UTF-8 decoder: each maximal
    subpart of an ill-formed sequence becomes one U+FFFD.
    """
    out = []
    append = out.append
    n = len(b)
    i = 0
    needed = 0      # continuation bytes still expected
    seen = 0        # continuation bytes consumed so far
    cp = 0          # code point being accumulated
    lo = 0x80       # allowed range for the next continuation byte
    hi = 0xBF

    while i < n:
        byte = b[i]
        if needed == 0:
            if byte < 0x80:
                append(chr(byte))
                i += 1
            elif 0xC2 <= byte <= 0xDF:
                needed = 1
                cp = byte & 0x1F
                i += 1
            elif 0xE0 <= byte <= 0xEF:
                if byte == 0xE0:
                    lo = 0xA0          # reject overlong 3-byte forms
                elif byte == 0xED:
                    hi = 0x9F          # reject surrogates
                needed = 2
                cp = byte & 0x0F
                i += 1
            elif 0xF0 <= byte <= 0xF4:
                if byte == 0xF0:
                    lo = 0x90          # reject overlong 4-byte forms
                elif byte == 0xF4:
                    hi = 0x8F          # reject above U+10FFFF
                needed = 3
                cp = byte & 0x07
                i += 1
            else:
                append('�')
                i += 1
        else:
            if lo <= byte <= hi:
                lo = 0x80
                hi = 0xBF
                cp = (cp << 6) | (byte & 0x3F)
                seen += 1
                i += 1
                if seen == needed:
                    append(chr(cp))
                    needed = 0
                    seen = 0
                    cp = 0
            else:
                # Sequence cut short: emit one U+FFFD for the maximal
                # subpart and reprocess this byte from the start state.
                append('�')
                needed = 0
                seen = 0
                cp = 0
                lo = 0x80
                hi = 0xBF

    if needed != 0:
        append('�')
    return ''.join(out)
