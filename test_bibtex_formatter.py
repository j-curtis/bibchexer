import unittest
from pathlib import Path
from unittest.mock import patch

import bibchex as bf


SAMPLE = '''@article{smith2020,
  author = {Jos\\'{e} Smith},
  title = {A café study},
  year = {2020},
  doi = {10.1234/example.1},
  local-url = {file:///private/paper.pdf},
}
'''


class FormatterTests(unittest.TestCase):
    def test_only_local_url_is_changed(self):
        entries, issues = bf.parse(SAMPLE)
        self.assertFalse(issues)
        cleaned, count = bf.remove_local_urls(SAMPLE, entries)
        self.assertEqual(count, 1)
        self.assertNotIn("local-url", cleaned)
        self.assertEqual(cleaned, SAMPLE.replace("  local-url = {file:///private/paper.pdf},\n", ""))

    def test_final_pass_removes_trailing_entry_comma(self):
        source = '''@article{x,
  title = {Example},
  local-url = {file:///paper.pdf}
}
'''
        entries, _ = bf.parse(source)
        cleaned, _ = bf.remove_local_urls(source, entries)
        self.assertRegex(cleaned, r"title = \{Example\},\s*\}")
        cleaned, count = bf.remove_trailing_entry_commas(cleaned)
        self.assertEqual(count, 1)
        self.assertRegex(cleaned, r"title = \{Example\}\s*\}")
        self.assertNotRegex(cleaned, r"title = \{Example\},\s*\}")

    def test_final_pass_handles_parenthesized_entries(self):
        source = '@book(x,\n  title = "Example",\n)\n'
        cleaned, count = bf.remove_trailing_entry_commas(source)
        self.assertEqual(count, 1)
        self.assertEqual(cleaned, '@book(x,\n  title = "Example"\n)\n')

    def test_removes_abstract_and_keywords_from_multiple_entry_types(self):
        source = '''@book{book,
  title = {A Book},
  abstract = {Nested {content, including commas}},
  keywords = {one, two},
}
@inproceedings(paper,
  author = "An Author",
  KEYWORDS = "three; four",
  abstract = "Summary",
)
@phdthesis{thesis,
  title = {A Thesis},
  school = {A University},
}
'''
        entries, issues = bf.parse(source)
        self.assertFalse(issues)
        cleaned, counts = bf.remove_fields(source, entries, {"abstract", "keywords", "local-url"})
        cleaned, _ = bf.remove_trailing_entry_commas(cleaned)
        self.assertEqual(counts, {"abstract": 2, "keywords": 2, "local-url": 0})
        reparsed, reparse_issues = bf.parse(cleaned)
        self.assertFalse(reparse_issues)
        self.assertEqual([e.kind.lower() for e in reparsed], ["book", "inproceedings", "phdthesis"])
        self.assertNotRegex(cleaned.lower(), r"\b(?:abstract|keywords)\s*=")

    def test_selective_setting_can_keep_abstract_and_keywords(self):
        entries, _ = bf.parse(SAMPLE)
        cleaned, counts = bf.remove_fields(SAMPLE, entries, {"local-url"})
        self.assertEqual(counts, {"local-url": 1})
        self.assertIn("title = {A café study}", cleaned)

    def test_unicode_in_removed_fields_is_not_reported(self):
        source = '''@article{x,
  title = {ASCII title},
  abstract = {Contains μ and café},
  keywords = {naïve},
  local-url = {file:///résumé.pdf},
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        issues = bf.inspect_entries(
            source,
            entries,
            False,
            1,
            ignored_character_fields={"abstract", "keywords", "local-url"},
        )
        self.assertNotIn("non-ascii-unicode", {issue.code for issue in issues})

    def test_unicode_in_retained_fields_is_still_reported(self):
        source = '''@book{x,
  title = {Café},
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        issues = bf.inspect_entries(
            source,
            entries,
            False,
            1,
            ignored_character_fields={"abstract", "keywords", "local-url"},
        )
        unicode_issues = [issue for issue in issues if issue.code == "non-ascii-unicode"]
        self.assertEqual([(issue.entry, issue.field) for issue in unicode_issues], [("x", "title")])

    def test_latex_accents_are_not_counted_as_non_ascii(self):
        source = '''@article{x,
  author = {Genois, {\\'E}. and Kieferov{\\'a}, M. and Mandr{\\`a}, S. and O’Brien, T. E.},
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        issues = bf.inspect_entries(source, entries, False, 1)
        issue = next(item for item in issues if item.code == "non-ascii-unicode")
        self.assertEqual(issue.line, 2)
        self.assertEqual(issue.message, "Contains Unicode character(s): '’' (U+2019)")
        self.assertNotIn("latex-accent", {item.code for item in issues})

    def test_flags_unicode_and_latex_accents(self):
        entries, _ = bf.parse(SAMPLE)
        codes = {i.code for i in bf.inspect_entries(SAMPLE, entries, False, 1, report_latex_accents=True)}
        self.assertIn("non-ascii-unicode", codes)
        self.assertIn("latex-accent", codes)

    def test_latex_accents_are_suppressed_by_default(self):
        entries, _ = bf.parse(SAMPLE)
        codes = {i.code for i in bf.inspect_entries(SAMPLE, entries, False, 1)}
        self.assertNotIn("latex-accent", codes)

    def test_normalizes_common_doi_forms(self):
        self.assertEqual(bf.normalize_doi("{https://doi.org/10.1000/XYZ}"), "10.1000/XYZ")

    def test_recognizes_arxiv_dois(self):
        self.assertTrue(bf.is_arxiv_doi("10.48550/arXiv.2401.12345"))
        self.assertTrue(bf.is_arxiv_doi("10.48550/ARXIV.2401.12345"))
        self.assertFalse(bf.is_arxiv_doi("10.1103/PhysRevLett.1.1"))

    def test_unverified_arxiv_doi_is_review_not_error(self):
        source = '''@misc{preprint,
  title = {A Preprint},
  doi = {10.48550/arXiv.2401.12345}
}
'''
        entries, _ = bf.parse(source)
        with patch("bibchex.fetch_doi", return_value=(None, "HTTP 404")):
            issues = bf.inspect_entries(source, entries, True, 1)
        arxiv_issue = next(issue for issue in issues if issue.code == "arxiv-doi-not-verified")
        self.assertEqual(arxiv_issue.severity, "review")
        self.assertNotIn("doi-not-verified", {issue.code for issue in issues})

    def test_unverified_non_arxiv_doi_remains_error(self):
        source = '''@article{paper,
  title = {A Paper},
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        with patch("bibchex.fetch_doi", return_value=(None, "HTTP 404")):
            issues = bf.inspect_entries(source, entries, True, 1)
        issue = next(issue for issue in issues if issue.code == "doi-not-verified")
        self.assertEqual(issue.severity, "error")

    def test_malformed_field_is_reported(self):
        _, issues = bf.parse("@article{x,\n title {broken},\n}\n")
        self.assertIn("malformed-field", {i.code for i in issues})

    def test_double_braced_values_are_valid(self):
        source = '''@article{double-braces,
  title = {{A Protected Title}},
  note = {{ }},
  author = {{Family, Given} and {Other, Author}},
  abstract = {{Nested, removable {content}}},
  doi = {{10.1234/example}}
}
'''
        entries, parse_issues = bf.parse(source)
        self.assertFalse(parse_issues)
        self.assertEqual(len(entries), 1)
        self.assertEqual(len(entries[0].fields), 5)
        inspection = bf.inspect_entries(source, entries, False, 1)
        self.assertNotIn("malformed-field", {issue.code for issue in inspection})
        self.assertNotIn("unusual-value-format", {issue.code for issue in inspection})
        self.assertNotIn("invalid-doi-syntax", {issue.code for issue in inspection})
        cleaned, counts = bf.remove_fields(source, entries, {"abstract"})
        cleaned, _ = bf.remove_trailing_entry_commas(cleaned)
        reparsed, reparse_issues = bf.parse(cleaned)
        self.assertFalse(reparse_issues)
        self.assertEqual(len(reparsed[0].fields), 4)
        self.assertIn("title = {{A Protected Title}}", cleaned)
        self.assertIn("note = {{ }}", cleaned)

    def test_error_output_contains_source_line(self):
        _, issues = bf.parse("\n\n@article{x,\n  title = {Unclosed}\n")
        issue = next(i for i in issues if i.code == "unclosed-entry")
        self.assertEqual(issue.line, 3)
        rendered = bf.format_issue(issue, Path("references.bib"))
        self.assertIn("references.bib:3", rendered)

    def test_report_order_puts_errors_first_and_doi_differences_last(self):
        issues = [
            bf.Issue("review", "doi-title-mismatch", "DOI difference", "c", "title", 3),
            bf.Issue("review", "non-ascii-unicode", "Character", "a", "author", 1),
            bf.Issue("error", "unclosed-entry", "Syntax", line=9),
            bf.Issue("warning", "unusual-value-format", "Formatting", "b", "title", 2),
        ]
        ordered = sorted(issues, key=bf.issue_sort_key)
        self.assertEqual(
            [issue.code for issue in ordered],
            ["unclosed-entry", "unusual-value-format", "non-ascii-unicode", "doi-title-mismatch"],
        )

    def test_terminal_color_is_optional(self):
        issue = bf.Issue("error", "test-error", "Example", line=4)
        plain = bf.format_issue(issue, Path("references.bib"))
        colored = bf.format_issue(issue, Path("references.bib"), color=True)
        self.assertNotIn("\033[", plain)
        self.assertIn("\033[31;1m", colored)
        self.assertTrue(colored.endswith("\033[0m"))


if __name__ == "__main__":
    unittest.main()
