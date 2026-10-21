def parse_csv(text):
    n = len(text)
    records = []
    i = 0
    while i < n:
        record = []
        while True:
            if i < n and text[i] == '"':
                i += 1
                buf = []
                while True:
                    j = text.find('"', i)
                    if j < 0:
                        raise ValueError("unterminated quoted field")
                    buf.append(text[i:j])
                    if j + 1 < n and text[j + 1] == '"':
                        buf.append('"')
                        i = j + 2
                    else:
                        i = j + 1
                        break
                field = "".join(buf)
                if i < n:
                    c = text[i]
                    if not (c == ',' or c == '\n' or (c == '\r' and text[i + 1:i + 2] == '\n')):
                        raise ValueError("garbage after closing quote")
            else:
                j = i
                while j < n:
                    c = text[j]
                    if c == ',' or c == '\n' or (c == '\r' and text[j + 1:j + 2] == '\n'):
                        break
                    j += 1
                field = text[i:j]
                i = j
            record.append(field)
            if i >= n:
                break
            c = text[i]
            if c == ',':
                i += 1
                continue
            if c == '\n':
                i += 1
            else:
                i += 2
            break
        records.append(record)
    return records
