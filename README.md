# Conservative BibTeX formatter

This command-line tool always removes `local-url` fields. By default it also
removes `abstract` and `keywords` fields. For `@article` entries, the cleaned
output retains only `title`, `author`, `year`, `journal`, `volume`, `pages`,
`doi`, `number`, `eprint`, `archiveprefix`, `primaryclass`, `eprinttype`, and
`eprintclass`. Field names are matched case-insensitively. It then removes any
comma left as the final non-whitespace character before an entry's closing
delimiter and reports possible problems for human review.

Checks include:

- valid UTF-8 plus non-ASCII Unicode characters and LaTeX accent commands;
- basic BibTeX entry/field structure, duplicate keys, and duplicate fields;
- required `@article` fields: `title`, `author`, `year`, `journal`, `volume`,
  and `pages`;
- DOI syntax and live Crossref resolution;
- DOI-based completion of missing article fields when Crossref supplies them;
- consistency checks for title, author, year, journal, volume, pages, and issue
  number against DOI metadata. Conflicts are reported as errors.

## Run

Python 3.9 or newer is sufficient; no packages need to be installed.

```sh
python3 bibchex.py references.bib
```

This creates `references.cleaned.bib`. The exit status is `1` when the results
contain an error, `0` when they contain only review items/warnings, and `2` for
an unreadable UTF-8 input file. A JSON report is written only when `--report`
is supplied.

Useful options:

```sh
# Review without writing cleaned BibTeX
python3 bibchex.py references.bib --review-only

# Skip network calls while testing or working offline
python3 bibchex.py references.bib --offline

# Keep abstract and keywords fields (local-url is still removed)
python3 bibchex.py references.bib --keep-abstract-keywords

# Preserve fields outside the default @article allowlist
python3 bibchex.py references.bib --keep-all-article-fields

# Include LaTeX accent commands in the review report
python3 bibchex.py references.bib --report-latex-accents

# Force or disable colored terminal output
python3 bibchex.py references.bib --color always
python3 bibchex.py references.bib --color never

# Convert supported Unicode accents, symbols, and formula scripts to LaTeX
python3 bibchex.py references.bib --latexify-unicode

# Select output and report paths
python3 bibchex.py references.bib -o cleaned.bib --report report.json

# Explicitly save the terminal findings as structured JSON
python3 bibchex.py references.bib --report references.report.json
```

The original file is never overwritten unless that exact path is explicitly
passed with `--output`. DOI metadata matching is intentionally conservative:
it fills missing article fields but never replaces an existing value. Existing
values that conflict with Crossref metadata are flagged as errors.
By default the cleaned file is written beside the input as
`INPUT_STEM.cleaned.bib`, regardless of where the script or shell is located.
Use `--output PATH` to choose another destination. `--review-only` suppresses
the cleaned file; the older name `--check-only` remains available as an alias.
Non-ASCII checks ignore fields scheduled for removal. Consequently, abstract
and keywords characters are checked only when `--keep-abstract-keywords` is
used. On `@article` entries, that option does not override the article
allowlist; combine it with `--keep-all-article-fields` to retain those fields.
Diagnostics include each Unicode code point and official Unicode name, making
invisible characters such as `U+2009 THIN SPACE` identifiable. LaTeX
accent-command reviews are suppressed unless
`--report-latex-accents` is supplied.

DOI enrichment is enabled by default and uses Crossref. For an `@article` with
a syntactically valid, resolvable DOI, missing `title`, `author`, `year`,
`journal`, `volume`, `pages`, and `number` fields are added when the returned
metadata contains them. `--offline` disables both enrichment and live DOI
consistency checks.

For `pages`, BibcheX accepts Crossref's `page` value or its `article-number`
electronic locator. If APS metadata contains neither, the final locator in a
`10.1103/...` DOI is used as the article's `pages` value.

`--latexify-unicode` is opt-in. It converts accented Latin letters, common
typographic/scientific symbols, and Unicode formula scripts (for example,
`H₂O` to `H$_{2}$O` and `Fe³⁺` to `Fe$^{3+}$`). It does not guess that plain
digits in text such as `H2O` are chemical subscripts. Non-breaking, thin, and
narrow no-break spaces are normalized to ordinary ASCII spaces.
The same option converts supported HTML formula forms such as `H<sub>2</sub>O`,
`Fe<sup>3+</sup>`, common entities such as `&alpha;`, and simple `<math>` or
`<mml:math>` fragments. Recognized markup is reported as
`html-formula-markup` when conversion is not enabled.

Issues in both terminal and JSON reports are ordered as errors, warnings,
general review items, and finally differences from current DOI metadata.
Terminal colors are enabled automatically when output is connected to an
interactive terminal; redirected output remains plain text.

An unverified DOI in arXiv's `10.48550/arXiv.*` namespace is reported as
`REVIEW arxiv-doi-not-verified`, not as an error, because these DOIs are
typically registered through DataCite and may be absent from Crossref.

## Test

```sh
python3 -m unittest -v
```
