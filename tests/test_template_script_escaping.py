"""`|forceescape` inside a <script> body emits &#34; instead of ", which is a JS
syntax error that kills the whole block — every onclick handler on the page goes
dead. It is only correct inside HTML attributes."""
import pathlib
import re

TEMPLATES = pathlib.Path(__file__).parent.parent / "bifrost" / "templates"


def test_no_forceescape_inside_script_bodies():
    offenders = []
    for path in TEMPLATES.rglob("*.html"):
        for block in re.findall(r"<script\b[^>]*>(.*?)</script>", path.read_text(), re.S):
            if "forceescape" in block:
                offenders.append(str(path.relative_to(TEMPLATES)))
    assert not offenders, f"forceescape in script body: {offenders}"
