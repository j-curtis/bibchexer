#!/usr/bin/env python3
"""BibTeX cleaner and validator with a compact default article schema."""

from __future__ import annotations

import argparse
import difflib
import html
import json
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


DOI_RE = re.compile(r"^10\.\d{4,9}/[-._;()/:A-Z0-9]+$", re.I)
ARTICLE_REQUIRED_FIELDS = ("title", "author", "year", "journal", "volume", "pages")
ARTICLE_ALLOWED_FIELDS = set(ARTICLE_REQUIRED_FIELDS) | {
    "doi",
    "number",
    "eprint",
    "archiveprefix",
    "primaryclass",
    "eprinttype",
    "eprintclass",
}
LATEX_ACCENT_RE = re.compile(r"\\(?:['`\"^~=.]|[Hckrublvd])\s*\{?[A-Za-z]")
ENTRY_START_RE = re.compile(r"(?m)^[^\S\r\n]*@([A-Za-z]+)\s*([({])")
DOI_DIFFERENCE_CODES = {
    "doi-title-mismatch",
    "doi-author-mismatch",
    "doi-year-mismatch",
    "doi-journal-mismatch",
    "doi-volume-mismatch",
    "doi-pages-mismatch",
    "doi-number-mismatch",
}
ANSI = {"error": "\033[31;1m", "warning": "\033[33;1m", "review": "\033[36m", "doi": "\033[35m", "reset": "\033[0m"}
LATEX_CHAR_MAP = {
    "Æ": r"{\AE}", "æ": r"{\ae}", "Œ": r"{\OE}", "œ": r"{\oe}",
    "Ø": r"{\O}", "ø": r"{\o}", "Å": r"{\AA}", "å": r"{\aa}",
    "Ł": r"{\L}", "ł": r"{\l}", "ß": r"{\ss}", "Ð": r"{\DH}",
    "ð": r"{\dh}", "Þ": r"{\TH}", "þ": r"{\th}",
    "’": "'", "‘": "`", "“": "``", "”": "''", "–": "--", "—": "---",
    "\u2009": " ", "\u202f": " ", "\u00a0": " ",
    "−": "$-$", "×": r"$\times$", "±": r"$\pm$", "µ": r"$\mu$", "μ": r"$\mu$",
    "α": r"$\alpha$", "β": r"$\beta$", "γ": r"$\gamma$", "δ": r"$\delta$",
    "ε": r"$\epsilon$", "θ": r"$\theta$", "λ": r"$\lambda$", "π": r"$\pi$",
    "σ": r"$\sigma$", "τ": r"$\tau$", "ω": r"$\omega$",
    "≈": r"$\approx$", "≲": r"$\lesssim$", "≳": r"$\gtrsim$", "≫": r"$\gg$",
    "≡": r"$\equiv$", "∝": r"$\propto$", "∼": r"$\sim$", "©": r"{\copyright}",
}
COMBINING_ACCENTS = {
    "\u0300": "`", "\u0301": "'", "\u0302": "^", "\u0303": "~",
    "\u0304": "=", "\u0306": "u", "\u0307": ".", "\u0308": '"',
    "\u030a": "r", "\u030b": "H", "\u030c": "v", "\u0327": "c", "\u0328": "k",
}
SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", "0123456789+-=()")
SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾", "0123456789+-=()")
SUBSCRIPT_CHARS = set("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎")
SUPERSCRIPT_CHARS = set("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾")
HTML_FORMULA_RE = re.compile(r"<(?:/?(?:sub|sup)\b|/?(?:mml:)?math\b|(?:mml:)?(?:mi|mn|mo|mtext)\b)|&(?:#\d+|#x[0-9a-f]+|alpha|beta|gamma|delta|epsilon|theta|lambda|mu|pi|sigma|tau|omega|minus|times|plusmn|le|ge|nbsp);", re.I)


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    entry: str | None = None
    field: str | None = None
    line: int | None = None


@dataclass
class Field:
    name: str
    value: str
    start: int
    end: int
    line: int


@dataclass
class Entry:
    kind: str
    key: str
    start: int
    end: int
    fields: list[Field]
    line: int


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def matching_close(text: str, opening: int, opener: str) -> int | None:
    closer = "}" if opener == "{" else ")"
    depth, quote, escaped = 1, False, False
    for i in range(opening + 1, len(text)):
        ch = text[i]
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"':
            quote = not quote
        elif not quote and ch == opener:
            depth += 1
        elif not quote and ch == closer:
            depth -= 1
            if depth == 0:
                return i
    return None


def split_top_level(text: str, base: int) -> list[tuple[str, int, int]]:
    parts: list[tuple[str, int, int]] = []
    start, braces, parens, quote, escaped = 0, 0, 0, False, False
    for i, ch in enumerate(text):
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == '"' and braces == 0:
            quote = not quote
        elif not quote:
            if ch == "{": braces += 1
            elif ch == "}" and braces: braces -= 1
            elif ch == "(": parens += 1
            elif ch == ")" and parens: parens -= 1
            elif ch == "," and braces == 0 and parens == 0:
                parts.append((text[start:i], base + start, base + i + 1))
                start = i + 1
    if text[start:].strip():
        parts.append((text[start:], base + start, base + len(text)))
    return parts


def parse(text: str) -> tuple[list[Entry], list[Issue]]:
    entries, issues = [], []
    for match in ENTRY_START_RE.finditer(text):
        kind, opener = match.group(1), match.group(2)
        opening = match.end() - 1
        end = matching_close(text, opening, opener)
        if end is None:
            issues.append(Issue("error", "unclosed-entry", "Entry has no matching closing delimiter.", line=line_number(text, match.start())))
            continue
        body = text[opening + 1:end]
        parts = split_top_level(body, opening + 1)
        if not parts:
            continue
        key = parts[0][0].strip()
        fields: list[Field] = []
        for raw, start, field_end in parts[1:]:
            if not raw.strip():
                continue
            fm = re.match(r"\s*([A-Za-z][\w-]*)\s*=\s*(.*?)\s*,?\s*$", raw, re.S)
            if not fm:
                issues.append(Issue("error", "malformed-field", "Could not parse field; check commas, braces, and '='.", key, line=line_number(text, start)))
                continue
            fields.append(Field(fm.group(1), fm.group(2), start, field_end, line_number(text, start + fm.start(1))))
        entries.append(Entry(kind, key, match.start(), end + 1, fields, line_number(text, match.start())))
    return entries, issues


def unwrap(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        value = value[1:-1].strip()
    # Multiple complete outer brace layers are legal (and often used to
    # preserve capitalization). Remove them for validation only; source text
    # is never rewritten by this helper.
    while len(value) >= 2 and value[0] == "{" and value[-1] == "}":
        depth, escaped, complete_outer = 0, False, True
        for index, char in enumerate(value):
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0 and index != len(value) - 1:
                    complete_outer = False
                    break
        if not complete_outer or depth != 0:
            break
        value = value[1:-1].strip()
    return value


def latexify_unicode(value: str) -> tuple[str, int]:
    """Convert supported Unicode text and formula glyphs to LaTeX."""
    output: list[str] = []
    changes = 0
    index = 0
    while index < len(value):
        char = value[index]
        if char in SUBSCRIPT_CHARS or char in SUPERSCRIPT_CHARS:
            chars = SUBSCRIPT_CHARS if char in SUBSCRIPT_CHARS else SUPERSCRIPT_CHARS
            end = index
            while end < len(value) and value[end] in chars:
                end += 1
            run = value[index:end]
            converted = run.translate(SUBSCRIPT_MAP if char in SUBSCRIPT_CHARS else SUPERSCRIPT_MAP)
            output.append("$_{" + converted + "}$" if char in SUBSCRIPT_CHARS else "$^{" + converted + "}$")
            changes += len(run)
            index = end
            continue
        if char in LATEX_CHAR_MAP:
            output.append(LATEX_CHAR_MAP[char])
            changes += 1
            index += 1
            continue
        decomposed = unicodedata.normalize("NFD", char)
        if ord(char) > 127 and len(decomposed) >= 2 and decomposed[0].isascii() and all(mark in COMBINING_ACCENTS for mark in decomposed[1:]):
            converted = decomposed[0]
            for mark in decomposed[1:]:
                converted = "{\\" + COMBINING_ACCENTS[mark] + converted + "}"
            output.append(converted)
            changes += 1
        else:
            output.append(char)
        index += 1
    return "".join(output), changes


def latexify_html_formulae(value: str) -> tuple[str, int]:
    """Convert supported HTML/MathML formula fragments to inline LaTeX."""
    changes = 0

    def math_replacement(match: re.Match[str]) -> str:
        nonlocal changes
        inner = re.sub(r"<[^>]+>", "", match.group(1))
        decoded = html.unescape(inner)
        converted, unicode_changes = latexify_unicode(decoded)
        changes += 1 + unicode_changes
        return "$" + converted.replace("$", "") + "$"

    value = re.sub(r"<(?:mml:)?math\b[^>]*>(.*?)</(?:mml:)?math\s*>", math_replacement, value, flags=re.I | re.S)

    def script_replacement(match: re.Match[str]) -> str:
        nonlocal changes
        kind, inner = match.group(1).lower(), match.group(2)
        inner = re.sub(r"<[^>]+>", "", html.unescape(inner))
        converted, unicode_changes = latexify_unicode(inner)
        changes += 1 + unicode_changes
        return ("$_{" if kind == "sub" else "$^{") + converted.replace("$", "") + "}$"

    value = re.sub(r"<(sub|sup)\b[^>]*>(.*?)</\1\s*>", script_replacement, value, flags=re.I | re.S)

    def entity_replacement(match: re.Match[str]) -> str:
        nonlocal changes
        decoded = html.unescape(match.group(0))
        if decoded == match.group(0):
            return match.group(0)
        special = {"&": r"\&", "<": "$<$", ">": "$>$", "\u00a0": "~"}
        if decoded in special:
            converted = special[decoded]
        else:
            converted, _ = latexify_unicode(decoded)
        changes += 1
        return converted

    value = re.sub(r"&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);", entity_replacement, value, flags=re.I)
    return value, changes


def latexify_entry_fields(text: str) -> tuple[str, int, int]:
    """Latexify field values while preserving entry headers and formatting."""
    entries, _ = parse(text)
    replacements: list[tuple[int, int, str, int, int]] = []
    for entry in entries:
        for field in entry.fields:
            html_converted, html_changes = latexify_html_formulae(field.value)
            converted, unicode_changes = latexify_unicode(html_converted)
            if html_changes or unicode_changes:
                value_start = field.start + text[field.start:field.end].find(field.value)
                replacements.append((value_start, value_start + len(field.value), converted, unicode_changes, html_changes))
    unicode_total = html_total = 0
    for start, end, converted, unicode_changes, html_changes in sorted(replacements, reverse=True):
        text = text[:start] + converted + text[end:]
        unicode_total += unicode_changes
        html_total += html_changes
    return text, unicode_total, html_total


def normalize_doi(value: str) -> str:
    value = unwrap(value).strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.I)
    return urllib.parse.unquote(value).strip().rstrip(".,")


def is_arxiv_doi(doi: str) -> bool:
    """Return whether a normalized DOI is in arXiv's DataCite DOI namespace."""
    return doi.lower().startswith("10.48550/arxiv.")


def norm_words(value: str) -> str:
    value = re.sub(r"\\[A-Za-z]+|[{}\\]", "", value)
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


def similar(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, norm_words(a), norm_words(b)).ratio()


def unicode_character_description(char: str) -> str:
    """Describe a non-ASCII character even when its glyph is invisible."""
    codepoint = f"U+{ord(char):04X}"
    name = unicodedata.name(char, "UNKNOWN CHARACTER")
    escaped = char.encode("unicode_escape").decode("ascii")
    return f"'{escaped}' ({codepoint} {name})"


def fetch_doi(doi: str, timeout: float) -> tuple[dict[str, Any] | None, str | None]:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    req = urllib.request.Request(url, headers={"User-Agent": "bibtex-conservative-checker/1.0 (mailto:unknown@example.invalid)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response).get("message", {}), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return None, str(exc)


def crossref_year(metadata: dict[str, Any]) -> str:
    for date_name in ("published-print", "published-online", "issued"):
        parts = metadata.get(date_name, {}).get("date-parts", [])
        if parts and parts[0]:
            return str(parts[0][0])
    return ""


def crossref_authors(metadata: dict[str, Any]) -> str:
    authors = []
    for author in metadata.get("author", []):
        family = str(author.get("family", "")).strip()
        given = str(author.get("given", "")).strip()
        name = ", ".join(part for part in (family, given) if part)
        if name:
            authors.append(name)
    return " and ".join(authors)


def crossref_pages(metadata: dict[str, Any]) -> str:
    """Return a page range or electronic article locator suitable for BibTeX."""
    page = str(metadata.get("page", "")).strip()
    if page:
        return page
    article_number = str(metadata.get("article-number", "")).strip()
    if article_number:
        return article_number
    doi = str(metadata.get("DOI", "")).strip()
    if doi.lower().startswith("10.1103/"):
        locator = doi.rsplit(".", 1)[-1]
        if locator != doi and re.fullmatch(r"[A-Za-z]?\d+", locator):
            return locator
    return ""


def crossref_article_fields(metadata: dict[str, Any]) -> dict[str, str]:
    """Map Crossref work metadata to fields useful for an article entry."""
    mapping = {
        "title": " ".join(metadata.get("title", [])).strip(),
        "author": crossref_authors(metadata),
        "year": crossref_year(metadata),
        "journal": " ".join(metadata.get("container-title", [])).strip(),
        "volume": str(metadata.get("volume", "")).strip(),
        "pages": crossref_pages(metadata),
        "number": str(metadata.get("issue", "")).strip(),
    }
    return {name: value for name, value in mapping.items() if value}


def enrich_articles_from_doi(
    text: str,
    entries: list[Entry],
    timeout: float,
) -> tuple[str, dict[str, int], dict[str, tuple[dict[str, Any] | None, str | None]]]:
    """Fill missing article fields from resolvable DOI metadata."""
    changes: list[tuple[int, int, str]] = []
    added_counts: dict[str, int] = {}
    doi_results: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
    for entry in entries:
        if entry.kind.lower() != "article":
            continue
        fields_by_name = {field.name.lower(): field for field in entry.fields}
        values = {name: unwrap(field.value).strip() for name, field in fields_by_name.items()}
        doi = normalize_doi(values.get("doi", ""))
        if not DOI_RE.fullmatch(doi):
            continue
        if doi not in doi_results:
            doi_results[doi] = fetch_doi(doi, timeout)
        metadata, error = doi_results[doi]
        if error or metadata is None:
            continue
        remote_fields = crossref_article_fields(metadata)
        missing = [
            name
            for name in (*ARTICLE_REQUIRED_FIELDS, "number")
            if not values.get(name) and remote_fields.get(name)
        ]
        if not missing:
            continue
        absent = []
        for name in missing:
            field = fields_by_name.get(name)
            if field is None:
                absent.append(name)
                continue
            value_offset = text[field.start:field.end].find(field.value)
            value_start = field.start + value_offset
            changes.append((value_start, value_start + len(field.value), "{" + remote_fields[name] + "}"))
        if absent:
            closing = entry.end - 1
            insertion = closing - 1
            while insertion > entry.start and text[insertion].isspace():
                insertion -= 1
            prefix = "" if text[insertion] == "," else ","
            rendered = prefix + "".join(f"\n  {name} = {{{remote_fields[name]}}}," for name in absent)
            changes.append((insertion + 1, insertion + 1, rendered))
        for name in missing:
            added_counts[name] = added_counts.get(name, 0) + 1
    for start, end, rendered in sorted(changes, reverse=True):
        text = text[:start] + rendered + text[end:]
    return text, added_counts, doi_results


def inspect_entries(
    text: str,
    entries: list[Entry],
    online: bool,
    timeout: float,
    ignored_character_fields: set[str] | None = None,
    report_latex_accents: bool = False,
    latexify_supported_unicode: bool = False,
    doi_results: dict[str, tuple[dict[str, Any] | None, str | None]] | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    ignored_character_fields = {name.lower() for name in (ignored_character_fields or set())}
    seen: set[str] = set()
    for entry in entries:
        if not entry.key:
            issues.append(Issue("error", "missing-key", "Entry has no citation key.", line=entry.line))
        elif entry.key in seen:
            issues.append(Issue("error", "duplicate-key", "Citation key is duplicated.", entry.key, line=entry.line))
        seen.add(entry.key)
        names: set[str] = set()
        values = {f.name.lower(): unwrap(f.value) for f in entry.fields}
        for field in entry.fields:
            lname = field.name.lower()
            if lname in names:
                issues.append(Issue("error", "duplicate-field", "Field appears more than once.", entry.key, field.name, field.line))
            names.add(lname)
            value = unwrap(field.value)
            if not (field.value.strip().startswith(("{", '"')) or re.fullmatch(r"\d+|[A-Za-z][\w:-]*", field.value.strip())):
                issues.append(Issue("warning", "unusual-value-format", "Value is not braced, quoted, numeric, or a simple macro.", entry.key, field.name, field.line))
            review_value = value
            if latexify_supported_unicode:
                review_value = latexify_html_formulae(review_value)[0]
                review_value = latexify_unicode(review_value)[0]
            if HTML_FORMULA_RE.search(review_value):
                issues.append(Issue("review", "html-formula-markup", "Contains HTML/MathML formula markup; use --latexify-unicode to convert supported forms.", entry.key, field.name, field.line))
            character_check_value = review_value
            if lname not in ignored_character_fields and any(ord(ch) > 127 for ch in character_check_value):
                chars = " ".join(sorted({unicode_character_description(ch) for ch in character_check_value if ord(ch) > 127}))
                issues.append(Issue("review", "non-ascii-unicode", f"Contains Unicode character(s): {chars}", entry.key, field.name, field.line))
            if report_latex_accents and LATEX_ACCENT_RE.search(value):
                issues.append(Issue("review", "latex-accent", "Contains a LaTeX accent command; verify encoding and spelling.", entry.key, field.name, field.line))
        if entry.kind.lower() == "article":
            for required_field in ARTICLE_REQUIRED_FIELDS:
                if not values.get(required_field, "").strip():
                    issues.append(
                        Issue(
                            "error",
                            "missing-required-field",
                            f"@article entry is missing required field {required_field!r}.",
                            entry.key,
                            required_field,
                            entry.line,
                        )
                    )
        doi_field = next((f for f in entry.fields if f.name.lower() == "doi"), None)
        if not doi_field:
            issues.append(Issue("review", "missing-doi", "No DOI field; confirm whether this work has a DOI.", entry.key, "doi", entry.line))
            continue
        doi = normalize_doi(doi_field.value)
        if not DOI_RE.fullmatch(doi):
            issues.append(Issue("error", "invalid-doi-syntax", f"DOI has invalid syntax: {doi!r}", entry.key, doi_field.name, doi_field.line))
            continue
        if not online:
            continue
        if doi_results is not None and doi in doi_results:
            metadata, error = doi_results[doi]
        else:
            metadata, error = fetch_doi(doi, timeout)
        if error:
            if is_arxiv_doi(doi):
                if entry.kind.lower() != "misc":
                    issues.append(
                        Issue(
                            "review",
                            "arxiv-doi-not-verified",
                            f"arXiv DOI could not be verified through Crossref (it may be registered through DataCite): {error}",
                            entry.key,
                            doi_field.name,
                            doi_field.line,
                        )
                    )
            else:
                issues.append(Issue("error", "doi-not-verified", f"DOI could not be resolved through Crossref: {error}", entry.key, doi_field.name, doi_field.line))
            continue
        remote_fields = crossref_article_fields(metadata)
        difference_severity = "error" if entry.kind.lower() == "article" else "review"
        remote_title = remote_fields.get("title", "")
        if values.get("title") and remote_title and similar(values["title"], remote_title) < 0.72:
            issues.append(Issue(difference_severity, "doi-title-mismatch", f"DOI title differs (Crossref: {remote_title!r}).", entry.key, "title", entry.line))
        remote_year = remote_fields.get("year", "")
        if values.get("year") and remote_year and values["year"] != remote_year:
            issues.append(Issue(difference_severity, "doi-year-mismatch", f"Year differs (BibTeX {values['year']!r}, Crossref {remote_year!r}).", entry.key, "year", entry.line))
        remote_authors = remote_fields.get("author", "")
        if values.get("author") and remote_authors and similar(values["author"], remote_authors) < 0.55:
            issues.append(Issue(difference_severity, "doi-author-mismatch", f"Authors differ (Crossref: {remote_authors!r}).", entry.key, "author", entry.line))
        comparison_rules = {} if entry.kind.lower() != "article" else {
            "journal": ("doi-journal-mismatch", 0.72),
            "volume": ("doi-volume-mismatch", 1.0),
            "pages": ("doi-pages-mismatch", 1.0),
            "number": ("doi-number-mismatch", 1.0),
        }
        for field_name, (code, threshold) in comparison_rules.items():
            local_value = values.get(field_name, "")
            remote_value = remote_fields.get(field_name, "")
            if not local_value or not remote_value:
                continue
            matches = similar(local_value, remote_value) >= threshold if threshold < 1 else norm_words(local_value) == norm_words(remote_value)
            if not matches:
                issues.append(
                    Issue(
                        "error",
                        code,
                        f"{field_name.capitalize()} differs (BibTeX {local_value!r}, Crossref {remote_value!r}).",
                        entry.key,
                        field_name,
                        entry.line,
                    )
                )
    return issues


def remove_fields(text: str, entries: list[Entry], field_names: set[str]) -> tuple[str, dict[str, int]]:
    """Remove selected fields without rewriting any surviving field text."""
    wanted = {name.lower() for name in field_names}
    selected = [f for e in entries for f in e.fields if f.name.lower() in wanted]
    spans = [(field.start, field.end) for field in selected]
    counts = {name: 0 for name in sorted(wanted)}
    for field in selected:
        counts[field.name.lower()] += 1
    for start, end in sorted(spans, reverse=True):
        # The parser's span includes the field's trailing comma. Preserve all
        # surrounding text exactly; absorb one indentation-only blank fragment.
        if start < len(text) and text[start:end].startswith("\n"):
            pass
        text = text[:start] + text[end:]
    return text, counts


def remove_fields_by_policy(
    text: str,
    entries: list[Entry],
    field_names: set[str],
    strip_article_fields: bool = True,
) -> tuple[str, dict[str, int]]:
    """Remove global fields and, by default, fields outside the article allowlist."""
    globally_removed = {name.lower() for name in field_names}
    selected: list[Field] = []
    for entry in entries:
        restrict_entry = strip_article_fields and entry.kind.lower() == "article"
        for field in entry.fields:
            name = field.name.lower()
            if name in globally_removed or (restrict_entry and name not in ARTICLE_ALLOWED_FIELDS):
                selected.append(field)

    counts = {name: 0 for name in sorted(globally_removed)}
    for field in selected:
        name = field.name.lower()
        counts[name] = counts.get(name, 0) + 1
    for field in sorted(selected, key=lambda item: item.start, reverse=True):
        text = text[:field.start] + text[field.end:]
    return text, counts


def remove_local_urls(text: str, entries: list[Entry]) -> tuple[str, int]:
    """Backward-compatible helper for callers that only remove local-url."""
    cleaned, counts = remove_fields(text, entries, {"local-url"})
    return cleaned, counts["local-url"]


def remove_trailing_entry_commas(text: str) -> tuple[str, int]:
    """Remove commas that directly precede an entry's closing delimiter.

    This is deliberately a separate final pass: entry boundaries are parsed
    again after field removal, and only the last non-whitespace character in
    each entry can be changed.
    """
    entries, _ = parse(text)
    comma_offsets: list[int] = []
    for entry in entries:
        index = entry.end - 2  # character immediately before closing } or )
        while index > entry.start and text[index].isspace():
            index -= 1
        if text[index] == ",":
            comma_offsets.append(index)
    for index in reversed(comma_offsets):
        text = text[:index] + text[index + 1:]
    return text, len(comma_offsets)


def issue_sort_key(issue: Issue) -> tuple[int, int, str, str, int]:
    """Order actionable failures first and DOI metadata comparisons last."""
    if issue.severity == "error":
        group = 0
    elif issue.severity == "warning":
        group = 1
    elif issue.code in DOI_DIFFERENCE_CODES:
        group = 3
    else:
        group = 2
    return (group, issue.line or 0, issue.entry or "", issue.field or "", 0)


def format_issue(issue: Issue, source: Path, color: bool = False) -> str:
    """Render an issue with a clickable-style source path and line number."""
    location = str(source)
    if issue.line is not None:
        location += f":{issue.line}"
    subject = ":".join(x for x in (issue.entry, issue.field) if x)
    if subject:
        location += f" [{subject}]"
    rendered = f"{issue.severity.upper():7} {issue.code:24} {location} {issue.message}"
    if color:
        color_name = "doi" if issue.code in DOI_DIFFERENCE_CODES else issue.severity
        return ANSI.get(color_name, "") + rendered + ANSI["reset"]
    return rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="input .bib file")
    parser.add_argument("-o", "--output", type=Path, help="cleaned output (default: INPUT.cleaned.bib)")
    parser.add_argument("--report", type=Path, help="write a JSON report to this path (disabled by default)")
    parser.add_argument("--offline", action="store_true", help="skip live Crossref DOI validation")
    parser.add_argument("--timeout", type=float, default=10.0, help="seconds per DOI request")
    parser.add_argument(
        "--review-only",
        "--check-only",
        dest="review_only",
        action="store_true",
        help="run checks and print findings without writing a cleaned BibTeX file",
    )
    parser.add_argument(
        "--keep-abstract-keywords",
        action="store_true",
        help="preserve abstract and keywords fields (they are removed by default)",
    )
    parser.add_argument(
        "--keep-all-article-fields",
        action="store_true",
        help="disable the default @article field allowlist",
    )
    parser.add_argument(
        "--report-latex-accents",
        action="store_true",
        help="report LaTeX accent commands for human review (suppressed by default)",
    )
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="terminal color mode (default: auto)",
    )
    parser.add_argument(
        "--latexify-unicode",
        action="store_true",
        help="convert supported Unicode accents, symbols, and formula scripts to LaTeX",
    )
    args = parser.parse_args(argv)
    output = args.output or args.input.with_suffix(".cleaned.bib")
    report_path = args.report
    raw = args.input.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        failing_line = raw[:exc.start].count(b"\n") + 1
        issue = Issue("error", "invalid-utf8", f"File is not valid UTF-8: {exc}", line=failing_line)
        if report_path is not None:
            report_path.write_text(json.dumps({"input": str(args.input), "issues": [asdict(issue)]}, indent=2) + "\n")
        use_color = args.color == "always" or (args.color == "auto" and sys.stderr.isatty())
        print(format_issue(issue, args.input, use_color), file=sys.stderr)
        if report_path is not None:
            print(f"Report: {report_path}", file=sys.stderr)
        return 2
    entries, issues = parse(text)
    if not entries:
        issues.append(Issue("error", "no-entries", "No BibTeX entries were found."))
    added_fields: dict[str, int] = {}
    doi_results: dict[str, tuple[dict[str, Any] | None, str | None]] = {}
    if not args.offline:
        text, added_fields, doi_results = enrich_articles_from_doi(text, entries, args.timeout)
        entries, enrichment_parse_issues = parse(text)
        issues.extend(enrichment_parse_issues)
    fields_to_remove = {"file", "local-url"}
    if not args.keep_abstract_keywords:
        fields_to_remove.update({"abstract", "keywords"})
    article_fields_to_remove = {
        field.name.lower()
        for entry in entries
        if entry.kind.lower() == "article"
        for field in entry.fields
        if field.name.lower() not in ARTICLE_ALLOWED_FIELDS
    }
    ignored_character_fields = fields_to_remove | (set() if args.keep_all_article_fields else article_fields_to_remove)
    issues.extend(
        inspect_entries(
            text,
            entries,
            not args.offline,
            args.timeout,
            ignored_character_fields=ignored_character_fields,
            report_latex_accents=args.report_latex_accents,
            latexify_supported_unicode=args.latexify_unicode,
            doi_results=doi_results,
        )
    )
    issues.sort(key=issue_sort_key)
    cleaned, removed_fields = remove_fields_by_policy(
        text,
        entries,
        fields_to_remove,
        strip_article_fields=not args.keep_all_article_fields,
    )
    cleaned, trailing_commas_removed = remove_trailing_entry_commas(cleaned)
    unicode_characters_converted = 0
    html_formulae_converted = 0
    if args.latexify_unicode:
        cleaned, unicode_characters_converted, html_formulae_converted = latexify_entry_fields(cleaned)
    if not args.review_only:
        output.write_text(cleaned, encoding="utf-8")
    report = {"input": str(args.input), "output": None if args.review_only else str(output), "entries": len(entries), "fields_added_from_doi": added_fields, "fields_removed": removed_fields, "local_url_fields_removed": removed_fields.get("local-url", 0), "trailing_entry_commas_removed": trailing_commas_removed, "unicode_characters_converted": unicode_characters_converted, "html_formulae_converted": html_formulae_converted, "online_doi_checks": not args.offline, "issues": [asdict(i) for i in issues]}
    if report_path is not None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    use_color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty())
    for issue in issues:
        print(format_issue(issue, args.input, use_color))
    removal_summary = ", ".join(f"{count} {name}" for name, count in removed_fields.items())
    addition_summary = ", ".join(f"{count} {name}" for name, count in sorted(added_fields.items())) or "0"
    print(f"Checked {len(entries)} entries; added {addition_summary} field(s) from DOI metadata; removed {removal_summary} field(s) and {trailing_commas_removed} trailing comma(s); {len(issues)} issue(s).")
    if args.latexify_unicode:
        print(f"Converted {unicode_characters_converted} supported Unicode character(s) to LaTeX.")
        print(f"Converted {html_formulae_converted} supported HTML/MathML formula fragment(s) to LaTeX.")
    if report_path is not None:
        print(f"Report: {report_path}")
    return 1 if any(i.severity == "error" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
