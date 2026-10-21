def _esc(s):
    out = ['"']
    m = {'"': '\\"', '\\': '\\\\', '\b': '\\b', '\f': '\\f',
         '\n': '\\n', '\r': '\\r', '\t': '\\t'}
    for ch in s:
        if ch in m:
            out.append(m[ch])
        elif ord(ch) < 0x20:
            out.append('\\u%04x' % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return ''.join(out)


def _key(s):
    return s.encode('utf-16-be', 'surrogatepass')


def canonical_json(v):
    if v is None:
        return 'null'
    if v is True:
        return 'true'
    if v is False:
        return 'false'
    if isinstance(v, int):
        return str(int(v))
    if isinstance(v, str):
        return _esc(v)
    if isinstance(v, (list, tuple)):
        return '[' + ','.join(canonical_json(x) for x in v) + ']'
    if isinstance(v, dict):
        return '{' + ','.join(_esc(k) + ':' + canonical_json(v[k])
                              for k in sorted(v, key=_key)) + '}'
    raise TypeError('unsupported type: %r' % type(v))
