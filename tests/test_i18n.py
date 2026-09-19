"""Every text goes through t(): no key may be missing, none may be left over."""

import re
from pathlib import Path

from app.i18n import LOCALE, STRINGS

APP = Path(__file__).parent.parent / "app"
KEYS = set(STRINGS[LOCALE])
SOURCE = "".join(p.read_text() for p in [*APP.rglob("*.py"), *(APP / "templates").glob("*.html")] if p.name != "i18n.py")


def test_every_static_key_exists():
    asked = set(re.findall(r"""\bt\(\s*f?['"]([a-z0-9_.]+)['"]""", SOURCE))
    asked |= set(re.findall(r"""(?:LedgerError|HTTPException\(\d+,|error=)\s*['"]([a-z0-9_.]+\.[a-z0-9_.]+)['"]""", SOURCE))
    assert sorted(k for k in asked if k not in KEYS and not k.endswith(".")) == []  # 'per.' ~ x is a prefix, checked below


def test_no_key_is_left_over():
    prefixes = set(re.findall(r"""\bt\(\s*f?['"]([a-z0-9_.]+\.)(?:['"]\s*~|\{)""", SOURCE))  # t('per.' ~ days), t(f"tx.{type}")
    literal = lambda k: f'"{k}"' in SOURCE or f"'{k}'" in SOURCE  # noqa: E731
    assert sorted(k for k in KEYS if not literal(k) and not any(k.startswith(p) for p in prefixes)) == []


def test_all_placeholders_are_filled():
    for key, text in STRINGS[LOCALE].items():
        for name in re.findall(r"\{(\w+)\}", text):
            assert re.search(rf"\b{name}\s*=", SOURCE), f"{key}: nobody passes {name}="
