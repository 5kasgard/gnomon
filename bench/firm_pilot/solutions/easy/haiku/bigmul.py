"""Multiply two arbitrary-precision decimal integers given as strings.

Standard library only. The strings are never converted with int(); each
string is processed digit by digit. Large operands use Karatsuba
multiplication over digit coefficient lists, falling back to schoolbook
multiplication for small sizes.
"""

_KARATSUBA_CUTOFF = 32


def _school(a, b):
    """Schoolbook product of two coefficient lists (little-endian)."""
    res = [0] * (len(a) + len(b))
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                res[i + j] += x * y
    return res


def _padd(a, b):
    """Coefficient-wise sum of two lists of possibly different length."""
    if len(a) < len(b):
        a, b = b, a
    res = list(a)
    for i, y in enumerate(b):
        res[i] += y
    return res


def _sub_in(res, poly):
    """Subtract poly from res in place (res must be long enough)."""
    if len(res) < len(poly):
        res.extend([0] * (len(poly) - len(res)))
    for i, c in enumerate(poly):
        res[i] -= c


def _add_into(res, poly, shift):
    """Add poly into res starting at position shift, growing res as needed."""
    need = shift + len(poly)
    if len(res) < need:
        res.extend([0] * (need - len(res)))
    for i, c in enumerate(poly):
        res[shift + i] += c


def _poly_mul(a, b):
    """Polynomial product (exact integer coefficients) via Karatsuba."""
    if not a or not b:
        return []
    if min(len(a), len(b)) <= _KARATSUBA_CUTOFF:
        return _school(a, b)
    k = max(len(a), len(b)) // 2
    a0, a1 = a[:k], a[k:]
    b0, b1 = b[:k], b[k:]
    z0 = _poly_mul(a0, b0)
    z2 = _poly_mul(a1, b1)
    z1 = _poly_mul(_padd(a0, a1), _padd(b0, b1))
    _sub_in(z1, z0)
    _sub_in(z1, z2)
    res = []
    _add_into(res, z0, 0)
    _add_into(res, z1, k)
    _add_into(res, z2, 2 * k)
    return res


def _split_sign(s):
    """Return (is_negative, digit_string_without_sign_or_leading_zeros)."""
    neg = False
    if s.startswith("-"):
        neg = True
        s = s[1:]
    elif s.startswith("+"):
        s = s[1:]
    digits = s.lstrip("0")
    return neg, digits


def multiply(a: str, b: str) -> str:
    """Return the product of the decimal integers a and b as a decimal string."""
    neg_a, da = _split_sign(a)
    neg_b, db = _split_sign(b)
    if not da or not db:
        return "0"

    # Little-endian digit coefficient lists, one per decimal digit.
    ca = [ord(ch) - 48 for ch in reversed(da)]
    cb = [ord(ch) - 48 for ch in reversed(db)]

    prod = _poly_mul(ca, cb)

    # Carry propagation in base 10.
    out = []
    carry = 0
    for c in prod:
        total = c + carry
        out.append(total % 10)
        carry = total // 10
    while carry:
        out.append(carry % 10)
        carry //= 10
    while len(out) > 1 and out[-1] == 0:
        out.pop()

    if len(out) == 1 and out[0] == 0:
        return "0"

    digits = "".join(chr(d + 48) for d in reversed(out))
    if neg_a != neg_b:
        return "-" + digits
    return digits
