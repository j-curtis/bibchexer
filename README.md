# Conservative BibTeX formatter

This command-line tool always removes `local-url` fields. By default it also
removes `abstract` and `keywords` fields. It then removes any comma left as the
final non-whitespace character before an entry's closing delimiter. It reports
all other possible problems for human review.

Checks include:

- valid UTF-8 plus non-ASCII Unicode characters and LaTeX accent commands;
- basic BibTeX entry/field structure, duplicate keys, and duplicate fields;
- DOI syntax and live Crossref resolution;
- approximate title and author matching, and exact year matching, against the
  metadata returned for each DOI.

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

# Include LaTeX accent commands in the review report
python3 bibchex.py references.bib --report-latex-accents

# Force or disable colored terminal output
python3 bibchex.py references.bib --color always
python3 bibchex.py references.bib --color never

# Select output and report paths
python3 bibchex.py references.bib -o cleaned.bib --report report.json

# Explicitly save the terminal findings as structured JSON
python3 bibchex.py references.bib --report references.report.json
```

The original file is never overwritten unless that exact path is explicitly
passed with `--output`. DOI metadata matching is intentionally conservative:
it flags suspected mismatches but never rewrites bibliographic text.
By default the cleaned file is written beside the input as
`INPUT_STEM.cleaned.bib`, regardless of where the script or shell is located.
Use `--output PATH` to choose another destination. `--review-only` suppresses
the cleaned file; the older name `--check-only` remains available as an alias.
Non-ASCII checks ignore fields scheduled for removal. Consequently, abstract
and keywords characters are checked only when `--keep-abstract-keywords` is
used. LaTeX accent-command reviews are suppressed unless
`--report-latex-accents` is supplied.

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
