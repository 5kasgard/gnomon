from fractions import Fraction


def best_approx(x: Fraction, N: int) -> Fraction:
    x = Fraction(x)
    if x.denominator <= N:
        return x
    fl = x.numerator // x.denominator
    f = x - fl
    a, b = f.numerator, f.denominator  # 0 < a < b, b > N
    ln, ld = 0, 1
    hn, hd = 1, 1
    while True:
        k = min((a * ld - ln * b) // (hn * b - a * hd), (N - ld) // hd)
        if k > 0:
            ln += k * hn
            ld += k * hd
        k2 = min((hn * b - a * hd) // (a * ld - ln * b), (N - hd) // ld)
        if k2 > 0:
            hn += k2 * ln
            hd += k2 * ld
        if k == 0 and k2 == 0:
            break
    lo = Fraction(ln, ld) + fl
    hi = Fraction(hn, hd) + fl
    dl, dh = x - lo, hi - x
    if dl < dh:
        return lo
    if dh < dl:
        return hi
    if ld != hd:
        return lo if ld < hd else hi
    return lo
