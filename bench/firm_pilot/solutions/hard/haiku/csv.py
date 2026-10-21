def _at_record_end(text, i):
    """True if a record end ("\n" or "\r\n") starts at index i."""
    n = len(text)
    if i >= n:
        return False
    if text[i] == "\n":
        return True
    return text[i] == "\r" and i + 1 < n and text[i + 1] == "\n"


def _at_terminator(text, i):
    """True if index i is a comma, a record end, or the end of the text."""
    return i >= len(text) or text[i] == "," or _at_record_end(text, i)


def _read_quoted(text, i):
    """Read a quoted field body; i is the index just after the opening quote.

    Returns (field, index just after the closing quote).
    """
    n = len(text)
    parts = []
    while True:
        j = text.find('"', i)
        if j == -1:
            raise ValueError("unterminated quoted field")
        parts.append(text[i:j])
        if j + 1 < n and text[j + 1] == '"':
            parts.append('"')
            i = j + 2
            continue
        return "".join(parts), j + 1


def parse_csv(text: str) -> list[list[str]]:
    records = []
    n = len(text)
    if n == 0:
        return records
    i = 0
    while True:
        record = []
        while True:
            if i < n and text[i] == '"':
                field, i = _read_quoted(text, i + 1)
                if not _at_terminator(text, i):
                    raise ValueError("unexpected character after quoted field")
            else:
                start = i
                while i < n and not _at_terminator(text, i):
                    i += 1
                field = text[start:i]
            record.append(field)
            if i >= n:
                records.append(record)
                return records
            if text[i] == ",":
                i += 1
                continue
            # record end
            i += 1 if text[i] == "\n" else 2
            records.append(record)
            break
        if i >= n:
            return records
