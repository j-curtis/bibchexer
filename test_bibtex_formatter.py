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
    CROSSREF_ARTICLE = {
        "title": ["A Crossref Title"],
        "author": [{"given": "Ada", "family": "Author"}],
        "published-print": {"date-parts": [[2026, 8, 20]]},
        "container-title": ["Journal of Tests"],
        "volume": "12",
        "issue": "3",
        "page": "10-20",
    }

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

    def test_default_article_policy_keeps_only_allowed_fields(self):
        source = '''@article{paper,
  title = {A Paper},
  author = {Ada Author},
  year = {2026},
  journal = {Journal of Tests},
  volume = {4},
  number = {2},
  pages = {1--9},
  doi = {10.1234/example},
  eprint = {2608.01234},
  archivePrefix = {arXiv},
  primaryClass = {astro-ph.GA},
  eprinttype = {arxiv},
  eprintclass = {astro-ph.GA},
  publisher = {Remove Me},
  note = {Remove Me Too}
}
@book{book,
  title = {A Book},
  publisher = {Keep Me}
}
'''
        entries, parse_issues = bf.parse(source)
        self.assertFalse(parse_issues)
        cleaned, counts = bf.remove_fields_by_policy(source, entries, {"local-url"})
        reparsed, reparse_issues = bf.parse(cleaned)
        self.assertFalse(reparse_issues)
        article_fields = {field.name.lower() for field in reparsed[0].fields}
        self.assertEqual(article_fields, bf.ARTICLE_ALLOWED_FIELDS)
        self.assertEqual(counts["publisher"], 1)
        self.assertEqual(counts["note"], 1)
        self.assertIn("publisher = {Keep Me}", cleaned)

    def test_article_allowlist_can_be_disabled(self):
        entries, _ = bf.parse(SAMPLE)
        cleaned, counts = bf.remove_fields_by_policy(
            SAMPLE,
            entries,
            {"local-url"},
            strip_article_fields=False,
        )
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
        self.assertEqual(issue.message, "Contains Unicode character(s): '\\u2019' (U+2019 RIGHT SINGLE QUOTATION MARK)")
        self.assertNotIn("latex-accent", {item.code for item in issues})

    def test_invisible_unicode_is_named_and_can_be_normalized(self):
        source = "McCulloch, I.\u2009P."
        self.assertEqual(
            bf.unicode_character_description("\u2009"),
            "'\\u2009' (U+2009 THIN SPACE)",
        )
        converted, changes = bf.latexify_unicode(source)
        self.assertEqual(converted, "McCulloch, I. P.")
        self.assertEqual(changes, 1)

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

    def test_missing_article_fields_are_filled_from_doi_metadata(self):
        source = '''@article{paper,
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        with patch("bibchex.fetch_doi", return_value=(self.CROSSREF_ARTICLE, None)) as fetch:
            enriched, counts, results = bf.enrich_articles_from_doi(source, entries, 1)
        fetch.assert_called_once_with("10.1234/example", 1)
        enriched_entries, parse_issues = bf.parse(enriched)
        self.assertFalse(parse_issues)
        values = {field.name.lower(): bf.unwrap(field.value) for field in enriched_entries[0].fields}
        self.assertEqual(values["title"], "A Crossref Title")
        self.assertEqual(values["author"], "Author, Ada")
        self.assertEqual(values["year"], "2026")
        self.assertEqual(values["journal"], "Journal of Tests")
        self.assertEqual(values["volume"], "12")
        self.assertEqual(values["pages"], "10-20")
        self.assertEqual(values["number"], "3")
        self.assertEqual(set(counts), {"title", "author", "year", "journal", "volume", "pages", "number"})
        self.assertIn("10.1234/example", results)

    def test_crossref_article_number_is_used_as_pages(self):
        metadata = {"page": "", "article-number": "012345", "DOI": "10.1234/example"}
        self.assertEqual(bf.crossref_pages(metadata), "012345")
        self.assertEqual(bf.crossref_article_fields(metadata)["pages"], "012345")

    def test_empty_pages_field_is_filled_without_creating_a_duplicate(self):
        source = '''@article{paper,
  pages = {},
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        with patch("bibchex.fetch_doi", return_value=(self.CROSSREF_ARTICLE, None)):
            enriched, counts, _ = bf.enrich_articles_from_doi(source, entries, 1)
        enriched_entries, parse_issues = bf.parse(enriched)
        self.assertFalse(parse_issues)
        pages = [field for field in enriched_entries[0].fields if field.name.lower() == "pages"]
        self.assertEqual(len(pages), 1)
        self.assertEqual(bf.unwrap(pages[0].value), "10-20")
        self.assertEqual(counts["pages"], 1)
        inspection = bf.inspect_entries(enriched, enriched_entries, False, 1)
        self.assertNotIn("duplicate-field", {issue.code for issue in inspection})

    def test_aps_doi_locator_is_used_when_page_metadata_is_absent(self):
        metadata = {"DOI": "10.1103/PhysRevD.110.123456"}
        self.assertEqual(bf.crossref_pages(metadata), "123456")

    def test_explicit_crossref_page_takes_precedence(self):
        metadata = {
            "page": "2142-2145",
            "article-number": "2142",
            "DOI": "10.1103/PhysRevLett.69.2142",
        }
        self.assertEqual(bf.crossref_pages(metadata), "2142-2145")

    def test_doi_metadata_conflicts_are_errors(self):
        source = '''@article{paper,
  title = {A Completely Different Work},
  author = {Someone Else},
  year = {1999},
  journal = {Unrelated Journal},
  volume = {99},
  number = {8},
  pages = {1--2},
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        results = {"10.1234/example": (self.CROSSREF_ARTICLE, None)}
        issues = bf.inspect_entries(source, entries, True, 1, doi_results=results)
        mismatches = {issue.code: issue for issue in issues if issue.code.startswith("doi-") and issue.code.endswith("-mismatch")}
        self.assertEqual(
            set(mismatches),
            {
                "doi-title-mismatch",
                "doi-author-mismatch",
                "doi-year-mismatch",
                "doi-journal-mismatch",
                "doi-volume-mismatch",
                "doi-pages-mismatch",
                "doi-number-mismatch",
            },
        )
        self.assertTrue(all(issue.severity == "error" for issue in mismatches.values()))

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

    def test_latexifies_accents_and_chemical_formula_scripts(self):
        value = "café, Ångström, H₂O, Fe³⁺, α–β"
        converted, changes = bf.latexify_unicode(value)
        self.assertEqual(
            converted,
            r"caf{\'e}, {\AA}ngstr{\"o}m, H$_{2}$O, Fe$^{3+}$, $\alpha$--$\beta$",
        )
        self.assertEqual(changes, 9)

    def test_latexify_mode_reports_only_unsupported_unicode(self):
        source = '''@article{x,
  title = {Café H₂O ☃},
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        issues = bf.inspect_entries(source, entries, False, 1, latexify_supported_unicode=True)
        unicode_issue = next(issue for issue in issues if issue.code == "non-ascii-unicode")
        self.assertEqual(unicode_issue.message, "Contains Unicode character(s): '\\u2603' (U+2603 SNOWMAN)")

    def test_latexification_preserves_entry_type_and_existing_latex(self):
        source = '''@misc{x,
  title = {{Café and H₂O with {\\'e}}},
  doi = {10.1234/example}
}
'''
        converted, changes, html_changes = bf.latexify_entry_fields(source)
        entries, issues = bf.parse(converted)
        self.assertFalse(issues)
        self.assertEqual(entries[0].kind.lower(), "misc")
        self.assertIn(r"{{Caf{\'e} and H$_{2}$O with {\'e}}}", converted)
        self.assertEqual(changes, 2)
        self.assertEqual(html_changes, 0)

    def test_identifies_and_converts_html_formulae(self):
        value = "H<sub>2</sub>O, Fe<sup>3+</sup>, &alpha; and <mml:math><mml:mi>d</mml:mi><mml:mo>=</mml:mo><mml:mn>2</mml:mn></mml:math>"
        converted, changes = bf.latexify_html_formulae(value)
        self.assertEqual(converted, r"H$_{2}$O, Fe$^{3+}$, $\alpha$ and $d=2$")
        self.assertEqual(changes, 4)

    def test_html_formulae_are_reviewed_unless_conversion_is_enabled(self):
        source = '''@article{x,
  title = {Water H<sub>2</sub>O},
  doi = {10.1234/example}
}
'''
        entries, _ = bf.parse(source)
        normal = bf.inspect_entries(source, entries, False, 1)
        converted = bf.inspect_entries(source, entries, False, 1, latexify_supported_unicode=True)
        self.assertIn("html-formula-markup", {issue.code for issue in normal})
        self.assertNotIn("html-formula-markup", {issue.code for issue in converted})

    def test_malformed_field_is_reported(self):
        _, issues = bf.parse("@article{x,\n title {broken},\n}\n")
        self.assertIn("malformed-field", {i.code for i in issues})

    def test_article_entries_require_bibliographic_fields(self):
        source = '''@article{complete,
  title = {Complete Article},
  author = {Ada Author},
  year = {2026},
  journal = {Journal of Tests},
  volume = {10},
  pages = {1--12}
}
@article{incomplete,
  title = {Incomplete Article},
  author = {},
  year = {2026}
}
@book{book,
  title = {Books Do Not Use the Article Requirements}
}
'''
        entries, parse_issues = bf.parse(source)
        self.assertFalse(parse_issues)
        issues = bf.inspect_entries(source, entries, False, 1)
        missing = [issue for issue in issues if issue.code == "missing-required-field"]
        self.assertEqual(
            [(issue.entry, issue.field) for issue in missing],
            [
                ("incomplete", "author"),
                ("incomplete", "journal"),
                ("incomplete", "volume"),
                ("incomplete", "pages"),
            ],
        )
        self.assertTrue(all(issue.severity == "error" for issue in missing))

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
