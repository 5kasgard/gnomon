def _fill(words, width):
    lines = []
    cur = ""
    for word in words:
        if len(word) > width:
            if cur:
                lines.append(cur)
                cur = ""
            for i in range(0, len(word), width):
                lines.append(word[i:i + width])
            continue
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def wrap(text: str, width: int) -> list[str]:
    paragraphs = []
    current = []
    for line in text.splitlines():
        words = line.split()
        if words:
            current.extend(words)
        elif current:
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)

    out = []
    for i, words in enumerate(paragraphs):
        if i:
            out.append("")
        out.extend(_fill(words, width))
    return out
