# Ingestion — manual QA notes (Phase 2)

Hand-written; the generated numbers are in [ingestion_report.md](./ingestion_report.md).

**Method.** Chunks of every document were read from `ingestion/data/chunks/*.jsonl` (text, tables, lists, small and large chunks). Tables were compared with the source PDF: the FDA chart against its extracted text layer, EFSA pages 5, 7 and 9 against rendered page images. Each extraction problem found was fixed in the extractor (general rules, no per-question special cases) and the document re-ingested. The automated check "numbers in extracted tables that are not on the source page" is 0 for every document.

## Problems found and fixed

| Document | Problem | Fix |
|---|---|---|
| FDA chart | Docling decoded the letter "d" as U+FFFD ("`�ays`") and found no table | Dropped Docling for this file; rebuilt the two-panel chart from PyMuPDF word positions: 13 tables (one per food category), 46 product rows, verified against the text layer. Indented variants ("opened"/"unopened") inherit their parent label; the "after opening / out of can" sub-header is folded into its row; the footer date "March 2018" is kept |
| FDA, USDA | `fda.gov` serves a 404 "abuse detection" page to browser User-Agents; `dietaryguidelines.gov` returns 403 to every script | Downloader uses a plain UA. USDA is fetched from the official ODPHP copy of the same 164-page PDF (`download_url` in the registry); citations keep the original `source_url`. If the registry URL for USDA stops working for readers, that is a registry decision, not an ingestion one |
| EFSA | Docling flagged a data row as header on tables with rotated column titles (Tables 4–7); fused cells ("160 1.5", "2.4 3.6", "7-11 1-3") in Tables 6 and 11; footnotes (d)/(e) of Table 7 on page 9 swallowed as a "picture" | Header rows containing a bare number are data; conservative fused-cell repair (checked against the rendered pages); generic recovery of any PDF text line missing from the extraction (3 lines recovered, attached to Table 7's notes) |
| Eatwell | Text wrapped around images was stored as clipped duplicate fragments ("holegrain food includes: nd chapatti…"); bold list continuations read as headings; a tips box and a 48x10 table were detected as sparse tables | Fragment stitching and de-duplication (sentences now complete, e.g. "cut the fat off of meat and the skin off of chicken…"); heading rules require a bold block / bold large face; sparse "tables" are rejected and kept as paragraphs |
| USDA, ICMR | Table-of-contents pages (garbled dot leaders) and running headers (alternating odd/even formats, sometimes mid-page); figure-panel residue detected as tables; 16pt "goals" list read as headings | Registry `exclude_pages` for the contents pages (USDA 4–7, ICMR 8–9); header/footer removal by repetition; chart-residue tables rejected; page-local body size |
| All PDFs | Chunks over the model's 512-token window (EFSA URL table: 920 tokens; ICMR +2 for `[CLS]`/`[SEP]`) | Budget includes special tokens and the embedded section prefix; oversize rows are split deliberately; 0 chunks over the limit |

## Known limitations (open, disclose in the README)

- **Registry years need a decision** (see "Registry year check" in the report): the WHO page retrieved was last modified 2026-01-26 (registry: 2020); the FDA chart is dated March 2018 inside the file (registry: 2023); the Eatwell PDF carries © 2020 and a 2023 file date (registry: 2018). The year is cited to users, so it should match the edition actually indexed. Not changed here.
- **Figures and charts**: no OCR and no chart reading. Cover pages and full-page figures (ICMR 1, 93, 95, 139; USDA 163; Eatwell 11) have no text layer. Chart panels (USDA "Current Intakes" figures) leave axis/legend residue as paragraphs.
- **Dropped labels**: repeated tab labels ("BIRTH THROUGH 23 MONTHS", "AGES 2-18") and some figure numbers ("Figure 1-3") are lost with the running headers; ~150 USDA and ~150 ICMR page-number/label digits are reported as "missing" for this reason. No nutrient value was found among those checked.
- **ICMR growth tables** (weight by age) have no column titles in the text layer (the "-2SD/-3SD" labels are not extracted); values are intact but unlabeled. ICMR is also a Q&A-style booklet whose sidebar questions interleave with body text, and its sections are mostly "Guideline N" level.
- **Section labels** are the two innermost headings and reflect each PDF's own styling; USDA/Eatwell sometimes nest oddly (e.g. Eatwell "Cutting down on saturated fat > Cutting down on salt").
- **Tiny chunks**: 12 chunks under 20 tokens (mostly USDA process-diagram labels such as "DEVELOP the Dietary Guidelines"); they are low-similarity to real questions and harmless to retrieval, but Phase 8 may merge or drop them.
- **EFSA wide tables** repeat a long header in every row-group chunk (Table 1: 5 chunks) to stay self-contained.
- **Smoke retrieval** (`python -m ingestion.sanity_check`): 10/10 questions return the right document and passage in the top 5; one (USDA added sugars) ranks a weak "Stage 3" chunk first, a candidate for Phase 8.
