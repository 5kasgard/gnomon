from fractions import Fraction


def best_approx(x: Fraction, N: int) -> Fraction:
    """Return the fraction p/q with 1 <= q <= N closest to x.

    Among equally close candidates, prefer the smallest denominator, then the
    smaller fraction.
    """
    x = Fraction(x)
    N = int(N)
    if N < 1:
        raise ValueError("N must be at least 1")

    if x.denominator <= N:
        return x

    # Split off the integer part; the best approximation shifts by the same
    # integer, leaving denominators and tie-breaks unchanged.
    m = x.denominator
    fl = x.numerator // m
    r = x.numerator - fl * m  # fractional part is r/m with 0 < r < m

    # Stern-Brocot descent with bulk steps: lower a/b < f < upper c/d,
    # with b*c - a*d == 1. Stop when the mediant needs denominator > N.
    a, b = 0, 1
    c, d = 1, 1
    while b + d <= N:
        Q = c * m - r * d  # > 0 : (upper - f) * m * d
        E = r * b - a * m  # > 0 : (f - lower) * m * b
        if Q < E:
            # f is above the mediant: move the lower bound up k times
            k = min((E - 1) // Q, (N - b) // d)
            a += k * c
            b += k * d
        else:
            # f is below the mediant: move the upper bound down k times
            k = min((Q - 1) // E, (N - d) // b)
            c += k * a
            d += k * b

    # The closest candidates are exactly the two neighbours a/b and c/d.
    # Distances: f - a/b = E/(m*b), c/d - f = Q/(m*d).
    E = r * b - a * m
    Q = c * m - r * d
    lhs = E * d
    rhs = Q * b
    if lhs < rhs:
        p, q = a, b
    elif lhs > rhs:
        p, q = c, d
    else:
        # Equal distance: smallest denominator, then the smaller fraction (lower).
        if b <= d:
            p, q = a, b
        else:
            p, q = c, d

    return Fraction(fl * q + p, q)
