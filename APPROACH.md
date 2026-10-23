# Approach Document — CardioTrack CT-200 Traceable QA Engine

## 1. OCR & Hierarchy Reconstruction Strategy
We use a hybrid extraction pipeline (`app/parser.py`) built on **PyMuPDF (`fitz`)** with an automatic **Tesseract OCR fallback (`pytesseract`)**:
- **Why this hybrid approach:** Regulatory device manuals frequently mix born-digital text pages with scanned schematic/specification pages or embedded tables. If a page yields `< 25` characters of native text, the pipeline rasterizes the page at 300 DPI and runs Tesseract OCR. Native tables are extracted via `page.find_tables()` and serialized into pipe-delimited Markdown so tabular thresholds (e.g., error codes E1–E5, pressure ranges) remain intact inside the enclosing section node.
- **Hierarchy Reconstruction:** Rather than trusting font sizes (which vary inconsistently across PDF exports and fail completely on OCR pages), we maintain an explicit **ancestry stack (`ancestry_stack`)** driven by structural patterns:
  1. Numbered sections (`1.`, `2.1`, `4.2.1`) establish explicit depth (`level = count('.') + 1`).
  2. Unnumbered ALL-CAPS subheadings (`WARNINGS`, `SPECIFICATIONS`, `TROUBLESHOOTING`) nest dynamically at `current_numbered_level + 1`.
  3. Running headers/footers (`Page X of Y`, `CardioTrack CT-200 Manual`) are stripped before paragraph aggregation so multi-page sections and bullet lists are not split across page boundaries.

### Structural Inconsistencies Discovered & Handled
1. **Duplicate Headings Under Different Parents:** Subsections like `Warnings` or `Specifications` appear multiple times under different parent chapters. A naive title-keyed parser overwrites or merges them. We construct a parent-qualified `canonical_path` (`ROOT > 2 Cuff Operation > Warnings` vs `ROOT > 3 Battery & Power > Warnings`) and append a sibling disambiguator `[2]` if two identical headings occur under the exact same parent.
2. **Mid-Section Page Breaks & Running Headers/Footers:** Page breaks occurring mid-paragraph or between a heading and its table caused early parser iterations to attach running footers to requirement text, polluting the SHA-256 `content_hash`. We added `PAGE_ARTIFACT_RE` filtering and validated via `test_page_headers_footers_and_inline_tables_preserved_without_breaking_hierarchy`.
3. **Mixed Numbered & Unnumbered Subsections:** Unnumbered callout blocks (`WARNINGS`, `CONTRAINDICATIONS`) lacked numeric prefixes and initially defaulted to top-level nodes (`level=1`), detaching them from the subsystem they governed. We resolved this by resolving their level relative to the nearest active numbered ancestor on the stack.

## 2. Data Model & Version Matching Strategy
We separate **identity** from **versioned state** in SQLite (`app/models.py`):
- `LogicalNode`: Holds the stable `id` and `canonical_path` across the document's lifecycle.
- `NodeVersion`: Holds the immutable snapshot (`heading`, `level`, `body_text`, `tables_markdown`, `content_hash`, `parent_logical_id`, `change_status`) for a specific `(logical_node_id, version_number)`.
- `Selection` & `SelectionItem`: Version-pinned pointers linking a named selection to exact `node_version_id` and `pinned_content_hash` values.
- `NoSQLGenerationStore` (`nosql_generations.json` / MongoDB-compatible document store): Stores variable-length LLM test suites alongside embedded `node_snapshots` so generations remain self-contained even if versions are later archived.

### Three-Tier Version Matching (`v1` → `v2`) & Where It Breaks
When `ct200_manual_v2.pdf` is ingested, `find_best_logical_node_match` reconciles each parsed section against existing `LogicalNode` records:
1. **Tier 1 (Exact Canonical Path):** Matches normalized `ROOT > ... > Heading`.
2. **Tier 2 / 3 (Fuzzy Canonical Path >= 0.86):** Catches minor heading edits (e.g., `2.1 Cuff Pressure Limits` → `2.1 Cuff Pressure Safety Limits`).
- **Where it breaks:** If an author reorganizes the document tree in `v2` by moving a subsection to a completely different chapter *and* simultaneously rewrites its heading (e.g., moving `2.1 Cuff Limits` to `5.4 Pneumatic Thresholds`), the canonical path similarity drops below `0.86` and the system treats it as a `DELETED` node in Chapter 2 and a newly `ADDED` node in Chapter 5, marking prior test cases tied to the old node as `HIGH_IMPACT_REMOVED`.

## 3. LLM Prompt Design, Structured Output Validation & Duplicate Policy
- **Prompt & Traceability Enforcement:** The prompt instructs the LLM to act as an IEC 62304 QA engineer and provides each selected node's `logical_node_id`, `canonical_path`, `heading`, `body_text`, and `tables`.
- **Malformed Output & Retry Strategy:** Every LLM response is validated against Pydantic's `LLMTestSuiteOutput` (`3 <= len(test_cases) <= 5`, non-vague `expected_result`, valid `priority` enum) plus a **referential integrity check** confirming every `source_logical_node_id` is in the selection. If JSON parsing, Pydantic validation, or ID verification fails, the system retries up to 3 times, injecting the exact `ValidationError` traceback into the retry prompt so the model self-corrects. If all 3 attempts fail, the API returns HTTP `502 Bad Gateway` without persisting corrupted data.
- **Duplicate Selection Submission Policy:** Submitting `/selections/{id}/generate` twice is **idempotent by default**—we hash the sorted `pinned_content_hash` values (`combined_source_hash`) and return the existing generation (`"cached": true`) to save LLM tokens and preserve audit stability. Callers who explicitly want fresh test ideas can pass `{"force_regenerate": true}`.

## 4. Staleness & Impact Detection (and Its Limits)
At retrieval time (`/generations/by-selection/{id}` or `/generations/by-node/{id}`), `enrich_with_staleness()` compares each test case's `pinned_content_hash` against the node's `content_hash` in the latest `DocumentVersion`:
- **Honest Limitation of Pure Hash Comparison:** A raw SHA-256 hash flips whether an editor fixes a typo ("the monitor" → "this monitor") or changes a life-critical threshold ("300 mmHg" → "250 mmHg"). To avoid treating cosmetic edits the same as clinical changes, `compute_node_diff` runs a secondary domain regex (`NUMERIC_UNIT_RE`) over units (`mmHg`, `seconds`, `bpm`, `kPa`, `E\d+`) and classifies staleness severity into `CRITICAL_THRESHOLD_CHANGE`, `LOW_WORDING_CHANGE`, or `HIGH_IMPACT_REMOVED`. Even so, a semantic negation without number changes ("must auto-deflate" → "must not auto-deflate") would be classified as `LOW_WORDING_CHANGE` by regex alone—with more time, we would add an LLM semantic-equivalence classifier on the unified diff.

---

## 5. Required Decision Log

1. **What's the one part of this system most likely to silently give wrong results without erroring? How would you catch it?**
   The hierarchy level inference for unnumbered ALL-CAPS lines in `parse_hierarchy_from_lines`: if a body paragraph contains a short uppercase warning or table header (e.g., `CAUTION: DO NOT SUBMERGE` on its own line), the parser will promote it to a child section node and attach subsequent paragraphs of the parent section under that warning node instead of the parent. I would catch this by writing a post-parse structural invariant check that flags any leaf section whose heading starts with callout tokens (`NOTE`, `CAUTION`) and steals more than 1 paragraph from an otherwise empty parent section, paired with a golden section-outline snapshot test against the PDF's Table of Contents.

2. **Where did you choose simplicity over correctness because of time, and what would break first if this went to production as-is?**
   I used a local JSON-file-backed document store (`NoSQLGenerationStore` reading/writing `nosql_generations.json`) and synchronous LLM HTTP calls inside the request cycle for zero-dependency portability. In production, concurrent `/generate` requests would cause a read-modify-write race condition overwriting `nosql_generations.json`, and slow LLM retries (up to 3 × 30s) would block FastAPI workers and trigger gateway timeouts.

3. **Name one input (to your parser, your versioning matcher, or your LLM call) that you did not handle, and what your system does when it sees it.**
   **Multi-column PDF layouts (two-column specification pages) or tables that span across a page break without repeating headers:** `page.get_text("text")` and `find_tables()` read blocks per page, so a table split across pages 6 and 7 with an intervening running header (`Page 7 of 12`) has its continuation rows emitted as pipe-delimited lines without the column header context, or if borderless, merged into `body_lines` as space-separated text—causing the node's `tables_markdown` to be truncated at the page break while the second half lands in `body_text`.