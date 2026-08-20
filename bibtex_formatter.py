#!/usr/bin/env python3
"""Conservative BibTeX cleaner and validator.

The only automatic edit is removal of ``local-url`` fields.  Everything else
is reported for human review.
"""

from __future__ import annotations

import argparse
import difflib
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
LATEX_ACCENT_RE = re.compile(r"\\(?:['`\"^~=.]|[Hckrublvd])\s*\{?[A-Za-z]")
ENTRY_START_RE = re.compile(r"(?m)^[^\S\r\n]*@([A-Za-z]+)\s*([({])")
DOI_DIFFERENCE_CODES = {"doi-title-mismatch", "doi-author-mismatch", "doi-year-mismatch"}
ANSI = {"error": "\033[31;1m", "warning": "\033[33;1m", "review": "\033[36m", "doi": "\033[35m", "reset": "\033[0m"}


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


def inspect_entries(
    text: str,
    entries: list[Entry],
    online: bool,
    timeout: float,
    ignored_character_fields: set[str] | None = None,
    report_latex_accents: bool = False,
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
            if lname not in ignored_character_fields and any(ord(ch) > 127 for ch in value):
                chars = " ".join(sorted({f"{ch!r} (U+{ord(ch):04X})" for ch in value if ord(ch) > 127}))
                issues.append(Issue("review", "non-ascii-unicode", f"Contains Unicode character(s): {chars}", entry.key, field.name, field.line))
            if report_latex_accents and LATEX_ACCENT_RE.search(value):
                issues.append(Issue("review", "latex-accent", "Contains a LaTeX accent command; verify encoding and spelling.", entry.key, field.name, field.line))
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
        metadata, error = fetch_doi(doi, timeout)
        if error:
            if is_arxiv_doi(doi):
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
        remote_title = " ".join(metadata.get("title", []))
        if values.get("title") and remote_title and similar(values["title"], remote_title) < 0.72:
            issues.append(Issue("review", "doi-title-mismatch", f"DOI title differs (Crossref: {remote_title!r}).", entry.key, "title", entry.line))
        remote_year = None
        for date_name in ("published-print", "published-online", "issued"):
            parts = metadata.get(date_name, {}).get("date-parts", [])
            if parts and parts[0]: remote_year = str(parts[0][0]); break
        if values.get("year") and remote_year and values["year"] != remote_year:
            issues.append(Issue("review", "doi-year-mismatch", f"Year differs (BibTeX {values['year']!r}, Crossref {remote_year!r}).", entry.key, "year", entry.line))
        remote_authors = " and ".join(" ".join(filter(None, (a.get("given"), a.get("family")))) for a in metadata.get("author", []))
        if values.get("author") and remote_authors and similar(values["author"], remote_authors) < 0.55:
            issues.append(Issue("review", "doi-author-mismatch", f"Authors differ (Crossref: {remote_authors!r}).", entry.key, "author", entry.line))
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
    fields_to_remove = {"local-url"}
    if not args.keep_abstract_keywords:
        fields_to_remove.update({"abstract", "keywords"})
    issues.extend(
        inspect_entries(
            text,
            entries,
            not args.offline,
            args.timeout,
            ignored_character_fields=fields_to_remove,
            report_latex_accents=args.report_latex_accents,
        )
    )
    issues.sort(key=issue_sort_key)
    cleaned, removed_fields = remove_fields(text, entries, fields_to_remove)
    cleaned, trailing_commas_removed = remove_trailing_entry_commas(cleaned)
    if not args.review_only:
        output.write_text(cleaned, encoding="utf-8")
    report = {"input": str(args.input), "output": None if args.review_only else str(output), "entries": len(entries), "fields_removed": removed_fields, "local_url_fields_removed": removed_fields.get("local-url", 0), "trailing_entry_commas_removed": trailing_commas_removed, "online_doi_checks": not args.offline, "issues": [asdict(i) for i in issues]}
    if report_path is not None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    use_color = args.color == "always" or (args.color == "auto" and sys.stdout.isatty())
    for issue in issues:
        print(format_issue(issue, args.input, use_color))
    removal_summary = ", ".join(f"{count} {name}" for name, count in removed_fields.items())
    print(f"Checked {len(entries)} entries; removed {removal_summary} field(s) and {trailing_commas_removed} trailing comma(s); {len(issues)} issue(s).")
    if report_path is not None:
        print(f"Report: {report_path}")
    return 1 if any(i.severity == "error" for i in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
