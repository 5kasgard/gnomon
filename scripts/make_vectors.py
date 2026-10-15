"""Generate shared canonical-JSON test vectors from the independent Python
reference implementation (python/tests/jcs_reference.py), never from the
Rust core, so the vectors are an outside check on it.

Usage: python3 scripts/make_vectors.py > tests/vectors/canonical.json
"""

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "python" / "tests"))
import jcs_reference  # noqa: E402

INPUTS = [
    'null',
    'true',
    '0',
    '-9007199254740991',
    '"plain"',
    '"esc \\" \\\\ \\/ \\b \\f \\n \\r \\t \\u0001 \\u001f"',
    '"\\u00e9 \\u20ac \\ud83d\\ude00"',
    '[]',
    '{}',
    '[1,[2,[3,{}]]]',
    '{ "b" : 2 , "a" : 1 }',
    # RFC 8785 section 3.2.3: key order by UTF-16 code units.
    '{"\\u20ac":"Euro Sign","\\r":"Carriage Return","\\ufb33":"Hebrew Letter Dalet With Dagesh",'
    '"1":"One","\\ud83d\\ude00":"Emoji: Grinning Face","\\u0080":"Control",'
    '"\\u00f6":"Latin Small Letter O With Diaeresis"}',
    '{"statement":"F = ma","confidence":8000,"truth":"true","frame":"mechanics","nested":{"z":[null,false],"a":"x"}}',
]

vectors = []
for text in INPUTS:
    canon = jcs_reference.canonical(json.loads(text))
    vectors.append({
        "input": text,
        "canonical": canon,
        "sha256": hashlib.sha256(canon.encode("utf-8")).hexdigest(),
    })
# Write UTF-8 bytes with \n line endings on every platform (Windows would
# otherwise use its legacy code page and \r\n).
text = json.dumps(vectors, ensure_ascii=False, indent=2) + "\n"
sys.stdout.buffer.write(text.encode("utf-8"))
