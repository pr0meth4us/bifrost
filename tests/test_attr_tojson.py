"""`|tojson` inside an HTML attribute must be followed by `|forceescape`.

Flask's tojson escapes <, >, & and ' — but NOT the double quote. Dropped into a
double-quoted attribute it closes the attribute early, so the browser keeps only
the fragment before the first quote and Alpine evaluates a truncated expression:

    @click="selected = {"                      <- the payment row click
    :class="selected && selected.id === "      <- the row highlight
    x-text="showSecrets ? "                    <- the reveal-secret button

Every one of those is a silently dead control. forceescape emits &#34;, which the
HTML parser turns back into a real quote inside the attribute value.

Attributes span multiple lines, so this scans the whole file, not line by line.
"""
import re, sys, pathlib

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / "bifrost" / "templates"

# An attribute value opens at ="  and runs to the next unescaped "
ATTR = re.compile(r'[\w:@\-\.\[\]]+="([^"]*)"', re.S)
TOJSON_OK = re.compile(r'\|\s*tojson[^}]*\|\s*forceescape')
TOJSON_ANY = re.compile(r'\|\s*tojson')


def offenders():
    bad = []
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        # Strip <script> blocks: tojson is correct and required there.
        stripped = re.sub(r"<script\b.*?</script>", "", text, flags=re.S | re.I)
        for m in ATTR.finditer(stripped):
            value = m.group(1)
            if TOJSON_ANY.search(value) and not TOJSON_OK.search(value):
                line = stripped[: m.start()].count("\n") + 1
                bad.append(f"{path.relative_to(TEMPLATES)}:{line}  {value.strip()[:70]}")
    return bad


def test_no_bare_tojson_in_attributes():
    found = offenders()
    assert not found, "bare |tojson in HTML attribute:\n  " + "\n  ".join(found)


if __name__ == "__main__":
    found = offenders()
    if found:
        print("FAIL:\n  " + "\n  ".join(found))
        sys.exit(1)
    print("ok: every |tojson in an attribute is forceescape'd")
