def utf8_decode(b: bytes) -> str:
    b = bytes(b)
    n = len(b)
    out = []
    i = 0
    while i < n:
        c = b[i]
        if c < 0x80:
            out.append(chr(c))
            i += 1
            continue
        if 0xC2 <= c <= 0xDF:
            need = 1
            lo, hi = 0x80, 0xBF
            cp = c & 0x1F
        elif 0xE0 <= c <= 0xEF:
            need = 2
            lo = 0xA0 if c == 0xE0 else 0x80
            hi = 0x9F if c == 0xED else 0xBF
            cp = c & 0x0F
        elif 0xF0 <= c <= 0xF4:
            need = 3
            lo = 0x90 if c == 0xF0 else 0x80
            hi = 0x8F if c == 0xF4 else 0xBF
            cp = c & 0x07
        else:
            out.append('�')
            i += 1
            continue
        j = i + 1
        ok = True
        for k in range(need):
            if j >= n:
                ok = False
                break
            d = b[j]
            if k == 0:
                if not (lo <= d <= hi):
                    ok = False
                    break
            elif not (0x80 <= d <= 0xBF):
                ok = False
                break
            cp = (cp << 6) | (d & 0x3F)
            j += 1
        if ok:
            out.append(chr(cp))
        else:
            out.append('�')
        i = j
    return ''.join(out)
