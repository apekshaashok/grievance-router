# Corpus

Three official documents, each converted from its PDF into a clause-marked text file that the chunker reads.
The PDFs are kept in `raw/` so anyone can check the text against the source.

| doc_id | File | Document | Version indexed | Source PDF (`raw/`) |
|---|---|---|---|---|
| `SEBI-SCORES` | `sebi_master_circular_grievance.txt` | SEBI circular SEBI/HO/OIAE/IGRD/CIR/P/2023/156, *Redressal of investor grievances through the SEBI Complaint Redressal (SCORES) Platform and linking it to Online Dispute Resolution platform* | 20-09-2023, in force 04-12-2023 | `sebi_scores_2023.pdf` (15 pp.) |
| `SEBI-MFR` | `sebi_mf_regulations_investor_servicing.txt` | SEBI (Mutual Funds) Regulations, 1996: selected definitions and the investor-servicing provisions (Reg. 25(15), 25(22)(b), 53, 59B, 60, 60A) | SEBI consolidated text, December 2024 | `sebi_mf_regs.pdf` (161 pp.) |
| `RBI-IOS` | `rbi_integrated_ombudsman_scheme.txt` | Reserve Bank - Integrated Ombudsman Scheme, 2021 | 2021, as amended 05-08-2022 | `rbi_ios_2021.pdf` (19 pp.) |

`raw/rbi_ios_salient.pdf` (RBI's two-page "salient features" sheet) is kept for reference only and is not indexed.

Provenance: rbidocs.rbi.org.in refused automated downloads, so `rbi_ios_2021.pdf` is the full Scheme text as republished
by a regulated bank (banks must publish it under clause 18). It carries RBI's footnotes for the 05-08-2022 amendments; check it
against the official PDF at rbidocs.rbi.org.in/rdocs/content/pdfs/RBIOS2021_121121.pdf if in doubt. The SCORES circular is
the copy circulated by NSE; the MF Regulations PDF is SEBI's own.

**Why the SCORES circular stands in for "SEBI's master circular on investor grievance redressal".** Circular 2023/156
rescinded the Master Circular SEBI/HO/OIAE/IGRD/P/CIR/2022/0150 of 07-11-2022 with effect from 04-12-2023 (paragraph 6,
indexed as `SEBI-SCORES#P6`). It is the text that now governs which complaints SEBI's system handles, the 21-day redressal
timeline (Annexure I) and the matters it does not deal with (Annexure II, clause 5).

**Why only part of the MF Regulations.** The brief asks for "the chapter on investor servicing". The 1996 Regulations have no
chapter by that name; the servicing obligations are spread over Chapter VII (Reg. 53, 59B, 60, 60A) and Reg. 25. Indexing
all 161 pages (mostly scheme investment limits) would add hundreds of passages that a grievance can never turn on.

## What was changed from the official text

* PDF-extraction artefacts were repaired (`compa ny` -> `company`, `Redress al` -> `Redressal`), footnote markers removed. No wording was changed.
* Where a provision is a lead sentence followed by a list (SCORES Annex II 5, RBI clause 10(2), MF Reg. 25(22)(b)), each
  item became its own clause **with the lead sentence repeated**, so every chunk reads on its own and can be cited alone.
  Example: `SEBI-SCORES#AII-5.3` = "The following types of complaints shall not be dealt through SCORES: Complaints falling
  under the purview of other regulatory bodies such as Reserve Bank of India ...".
* Two tables (SCORES Schedules II and V) are written row by row as sentences.
* Procedural material that no routing decision depends on was left out: the complaint form annex of the RBI Scheme,
  SCORES authentication procedures for designated bodies, RBI clauses on office locations and staffing.

## File format

```
# doc_id: RBI-IOS
# regulator: RBI
# title: Reserve Bank - Integrated Ombudsman Scheme, 2021
# version: 2021, as amended 05-08-2022
# source: corpus/raw/rbi_ios_2021.pdf

[CLAUSE 10(2)(a) | Prior complaint to the Regulated Entity | kind: maintainability]
A complaint under the Scheme shall not lie unless ...

[CLAUSE 9 | Grounds of complaint | kind: scope | refs: RBI-IOS#10(1), RBI-IOS#10(2)(a)]
...
```

* **Clause id** is the document's own numbering (`10(2)(a)`, `60A(1)`); for the SCORES circular `P` = paragraph,
  `AI`/`AII` = Annexure I/II, `SCH` = Schedule. A citation such as `SEBI-SCORES#AII-5.3` can be found in the PDF directly.
* **`kind`** is an annotation added at ingestion, one of `scope` (what a framework covers / who it applies to),
  `maintainability` (what bars, excludes or delays a complaint), `servicing` (a duty and timeline of the entity) or `general`.
  It is the main lever against the corpus difficulty (see `docs/architecture.md`, retrieval design).
* **`refs`** are cross-references. In-text references ("non-maintainable under clause 10", "clause 8(2)") are extracted
  automatically and resolved to the most specific indexed clause. A few links are curated where a provision is modified by
  one that does not cite it, e.g. SCORES paragraph 1 (what SCORES covers) -> Annex II 1, 3 and 5.x (what it does not).

## Re-building

```bash
python -m app.ingest --dump               # list every chunk with kind and refs (64 chunks)
python -m app.ingest                      # idempotent: embeds only new/changed chunks
python -m app.ingest --only RBI-IOS       # re-embed one document
python -m app.ingest --pdf-to-text corpus/raw/x.pdf   # page-marked draft of a new PDF, to annotate by hand
```
