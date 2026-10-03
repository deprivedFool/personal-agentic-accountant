"""
Jurisdiction packs: every country-specific number and list, kept out of the code.

A pack is ``jurisdictions/<code>/pack.yaml``. Countries without a pack use
``jurisdictions/generic/pack.yaml``: everything works, but tax figures are indicative.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

import yaml

PACKS_DIR = Path(__file__).resolve().parent.parent / "jurisdictions"
GENERIC = "generic"


def available_packs() -> Dict[str, str]:
    """Map pack code -> country name for every installed pack (excluding the generic one)."""
    packs = {}
    for path in sorted(PACKS_DIR.glob("*/pack.yaml")):
        code = path.parent.name
        if code != GENERIC:
            packs[code] = yaml.safe_load(path.read_text(encoding="utf-8"))["country"]
    return packs


def resolve_code(country: str) -> str:
    """Turn user input like 'Portugal', 'PT' or 'portugal ' into a pack code, or 'generic'."""
    wanted = country.strip().lower()
    for code, name in available_packs().items():
        if wanted in (code, name.lower()):
            return code
    return GENERIC


@lru_cache(maxsize=None)
def load_pack(code: str) -> Dict[str, Any]:
    path = PACKS_DIR / code / "pack.yaml"
    if not path.exists():
        path = PACKS_DIR / GENERIC / "pack.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def has_tax_tables(pack: Dict[str, Any]) -> bool:
    return "income_tax" in pack


def documents_for(pack: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list(pack.get("documents", []))
