"""
Reading, recognising and redacting the user's financial documents.

* ``read_file``     - PDF, Excel, CSV/TSV, JSON and text files. Bank statements are parsed
                      into transactions (see ``cfo.statements``).
* ``classify``      - matches a file to an entry of the jurisdiction's document checklist.
* ``redact``        - masks identifiers (tax numbers, IBANs, card numbers, emails, phones)
                      before anything is sent to a model.
"""

import csv
import io
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cfo.statements import parse_statement

TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".html", ".htm", ".yaml", ".yml"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".pdf", ".xlsx", ".xlsm"}
MAX_FOLDER_FILES = 200
MAX_TEXT_CHARS = 60_000  # per document, after extraction


@dataclass
class Document:
    """One file the user provided."""

    path: str
    kind: str  # text | table | statement | json
    content: Any
    doc_type: Optional[str] = None  # id from the jurisdiction's document checklist
    warnings: List[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return Path(self.path).name

    def preview_text(self, limit: int = 5000) -> str:
        if isinstance(self.content, str):
            return self.content[:limit]
        return json.dumps(self.content, ensure_ascii=False, default=str)[:limit]


def _plain(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def _read_pdf(path: Path) -> Tuple[str, List[str]]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", ["PDF support needs 'pypdf' (pip install pypdf); this file was skipped."]
    try:
        reader = PdfReader(str(path))
        text = "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception as exc:  # encrypted or malformed PDFs
        return "", [f"Couldn't read this PDF ({type(exc).__name__}). If it's password-protected, save an unprotected copy."]
    if not text.strip():
        return "", ["This PDF has no extractable text (probably a scan). Export a text-based PDF or type the key figures."]
    return text, []


def _read_xlsx(path: Path) -> Tuple[str, List[str]]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return "", ["Excel support needs 'openpyxl' (pip install openpyxl); export the sheet as CSV instead."]
    workbook = load_workbook(str(path), read_only=True, data_only=True)
    lines = []
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows(values_only=True):
            if any(cell is not None for cell in row):
                lines.append(";".join("" if cell is None else str(cell) for cell in row))
    return "\n".join(lines), []


def read_file(path: Path) -> Document:
    """Read one file into a ``Document``; problems are reported as warnings, never raised."""
    suffix = path.suffix.lower()
    warnings: List[str] = []
    if suffix == ".pdf":
        text, warnings = _read_pdf(path)
    elif suffix in (".xlsx", ".xlsm"):
        text, warnings = _read_xlsx(path)
    else:
        try:
            raw = path.read_bytes()
            try:
                text = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")  # common for Portuguese bank exports
        except OSError as exc:
            return Document(str(path), "text", "", warnings=[f"Couldn't open the file: {exc}"])

    if suffix == ".json":
        try:
            return Document(str(path), "json", json.loads(text), warnings=warnings)
        except json.JSONDecodeError:
            warnings.append("Invalid JSON; read as text.")
    if suffix in (".csv", ".tsv", ".xlsx", ".xlsm", ".txt"):
        transactions = parse_statement(text)
        if transactions:
            return Document(str(path), "statement", transactions, doc_type="bank_statements", warnings=warnings)
        if suffix in (".csv", ".tsv"):
            delimiter = "\t" if suffix == ".tsv" else (";" if text.count(";") > text.count(",") else ",")
            rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
            return Document(str(path), "table", rows, warnings=warnings)
    if len(text) > MAX_TEXT_CHARS:
        warnings.append(f"Long document: only the first {MAX_TEXT_CHARS:,} characters are used.")
        text = text[:MAX_TEXT_CHARS]
    return Document(str(path), "text", text, warnings=warnings)


def collect_files(paths: List[Path]) -> Tuple[List[Path], List[str]]:
    """Expand folders (recursively) into supported files."""
    files: List[Path] = []
    notes: List[str] = []
    for path in paths:
        if path.is_dir():
            found = sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
                           and not p.name.lower().startswith("readme")
                           and not any(part.startswith(".") for part in p.relative_to(path).parts))
            skipped = sum(1 for p in path.rglob("*") if p.is_file() and p.suffix.lower() not in SUPPORTED_SUFFIXES)
            if skipped:
                notes.append(f"{skipped} file(s) in {path} have unsupported types (e.g. images) and were skipped.")
            if len(found) > MAX_FOLDER_FILES:
                notes.append(f"Only the first {MAX_FOLDER_FILES} files in {path} are used.")
            files.extend(found[:MAX_FOLDER_FILES])
        elif path.is_file():
            files.append(path)
    return files, notes


def classify(document: Document, catalog: List[Dict[str, Any]]) -> Optional[str]:
    """Best-matching checklist id for a document, using its file name and content."""
    if document.doc_type:
        return document.doc_type
    haystack = _plain(document.name.replace("_", " ").replace("-", " ") + " " + document.preview_text())
    best, best_score = None, 0
    for entry in catalog:
        score = sum(haystack.count(_plain(k)) for k in entry.get("keywords", []))
        name_words = set(re.findall(r"[a-z0-9]+", _plain(document.name)))
        for keyword in entry.get("keywords", []):
            words = [w for w in re.findall(r"[a-z0-9]+", _plain(keyword)) if len(w) > 2]
            if words and all(w in name_words for w in words):
                score += 5
        if score > best_score:
            best, best_score = entry["id"], score
    return best


# --------------------------------------------------------------------------- redaction

def _valid_pt_nif(digits: str) -> bool:
    if len(digits) != 9 or digits[0] not in "123456789":
        return False
    total = sum(int(d) * w for d, w in zip(digits[:8], range(9, 1, -1)))
    check = 11 - total % 11
    return int(digits[8]) == (0 if check >= 10 else check)


def _luhn(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_NINE_DIGITS = re.compile(r"(?<![\d.,])(\d{3}[ ]?\d{3}[ ]?\d{3})(?![\d.,])")
_NISS = re.compile(r"(?<![\d.,])[12]\d{10}(?![\d.,])")
_PHONE = re.compile(r"(?:\+351[ ]?)?\b9[1236]\d[ ]?\d{3}[ ]?\d{3}\b")


def redact_text(text: str) -> Tuple[str, int]:
    """Mask identifiers in ``text``. Returns the new text and how many items were masked."""
    count = 0

    def sub(pattern: re.Pattern, label: str, check=None):
        nonlocal text, count
        def replace(match: re.Match) -> str:
            nonlocal count
            digits = re.sub(r"\D", "", match.group(0))
            if check and not check(digits):
                return match.group(0)
            count += 1
            return f"[{label}]"
        text = pattern.sub(replace, text)

    sub(_IBAN, "IBAN")
    sub(_EMAIL, "EMAIL")
    sub(_CARD, "CARD", _luhn)
    sub(_NINE_DIGITS, "TAX-ID", _valid_pt_nif)
    sub(_NISS, "SOCIAL-SECURITY-ID")
    sub(_PHONE, "PHONE")
    return text, count


def redact(value: Any) -> Tuple[Any, int]:
    """Redact every string inside ``value`` (str, list or dict)."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        total, out = 0, []
        for item in value:
            new, n = redact(item)
            out.append(new)
            total += n
        return out, total
    if isinstance(value, dict):
        total, out = 0, {}
        for key, item in value.items():
            new, n = redact(item)
            out[key] = new
            total += n
        return out, total
    return value, 0
