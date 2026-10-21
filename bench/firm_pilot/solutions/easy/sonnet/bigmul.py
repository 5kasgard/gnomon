def _limbs(s, w=4):
    out = []
    i = len(s)
    while i > 0:
        j = max(0, i - w)
        out.append(int(s[j:i]))
        i = j
    return out


def multiply(a: str, b: str) -> str:
    neg = False
    if a.startswith("-"):
        neg = not neg
        a = a[1:]
    if b.startswith("-"):
        neg = not neg
        b = b[1:]
    if a == "0" or b == "0" or a == "" or b == "":
        return "0"
    W = 4
    BASE = 10 ** W
    x = _limbs(a, W)
    y = _limbs(b, W)
    if len(x) < len(y):
        x, y = y, x
    res = [0] * (len(x) + len(y) + 1)
    for j, d in enumerate(y):
        if d == 0:
            continue
        for i, v in enumerate(x):
            res[i + j] += v * d
        if j % 1000 == 999:
            carry = 0
            for k in range(len(res)):
                t = res[k] + carry
                carry = t // BASE
                res[k] = t % BASE
    carry = 0
    for k in range(len(res)):
        t = res[k] + carry
        carry = t // BASE
        res[k] = t % BASE
    while len(res) > 1 and res[-1] == 0:
        res.pop()
    s = str(res[-1]) + "".join(str(v).zfill(W) for v in reversed(res[:-1]))
    return "-" + s if neg else s
