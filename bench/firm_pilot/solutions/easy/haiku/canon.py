"""RFC 8785 (JCS) canonical JSON serialization, standard library only."""

import sys

_SHORT_ESCAPES = {
    '"': '\\"',
    '\\': '\\\\',
    '\b': '\\b',
    '\f': '\\f',
    '\n': '\\n',
    '\r': '\\r',
    '\t': '\\t',
}


def _enc_str(s, out):
    out.append('"')
    for ch in s:
        esc = _SHORT_ESCAPES.get(ch)
        if esc is not None:
            out.append(esc)
            continue
        o = ord(ch)
        if o < 0x20:
            out.append('\\u%04x' % o)
        elif 0xD800 <= o <= 0xDFFF:
            # A lone surrogate has no well-formed UTF-8/UTF-16 encoding.
            raise ValueError('lone surrogate U+%04X cannot be canonicalized' % o)
        else:
            out.append(ch)
    out.append('"')


def _enc(v, out):
    if v is None:
        out.append('null')
    elif v is True:
        out.append('true')
    elif v is False:
        out.append('false')
    elif isinstance(v, bool):
        out.append('true' if v else 'false')
    elif isinstance(v, int):
        out.append(str(v))
    elif isinstance(v, str):
        _enc_str(v, out)
    elif isinstance(v, list):
        out.append('[')
        first = True
        for item in v:
            if not first:
                out.append(',')
            first = False
            _enc(item, out)
        out.append(']')
    elif isinstance(v, dict):
        for k in v:
            if not isinstance(k, str):
                raise TypeError('object keys must be str, got %s' % type(k).__name__)
        # Sort by UTF-16 code units: big-endian UTF-16 bytes compare exactly
        # like the sequence of code units.
        items = sorted(v.items(), key=lambda kv: kv[0].encode('utf-16-be'))
        out.append('{')
        first = True
        for k, val in items:
            if not first:
                out.append(',')
            first = False
            _enc_str(k, out)
            out.append(':')
            _enc(val, out)
        out.append('}')
    else:
        raise TypeError('unsupported type for canonical JSON: %s' % type(v).__name__)


def canonical_json(v):
    """Return the RFC 8785 canonical JSON text for v as a str."""
    get_limit = getattr(sys, 'get_int_max_str_digits', None)
    set_limit = getattr(sys, 'set_int_max_str_digits', None)
    old = None
    if get_limit is not None and set_limit is not None:
        old = get_limit()
        set_limit(0)  # allow arbitrarily large integers to be printed
    try:
        out = []
        _enc(v, out)
        return ''.join(out)
    finally:
        if old is not None:
            set_limit(old)
