def _wrap_par(words, width):
    out = []
    cur = ""
    for w in words:
        if len(w) > width:
            if cur:
                out.append(cur)
                cur = ""
            for i in range(0, len(w), width):
                out.append(w[i:i + width])
        elif not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            out.append(cur)
            cur = w
    if cur:
        out.append(cur)
    return out


def wrap(text, width):
    paras = []
    words = []
    for line in text.splitlines():
        if line.strip() == "":
            if words:
                paras.append(words)
                words = []
        else:
            words.extend(line.split())
    if words:
        paras.append(words)
    res = []
    for i, p in enumerate(paras):
        if i:
            res.append("")
        res.extend(_wrap_par(p, width))
    return res
