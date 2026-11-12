# Corpus

The three `.txt` files here are SHORT ILLUSTRATIVE SAMPLES so that the whole system runs
end to end on a clean clone. They paraphrase the shape of the real documents; they are not
the official text and the clause numbers and some values are not the real ones.

Before your demonstration (and before Evaluation 1) replace them with the real documents,
all free to download:

| doc_id | Source |
|---|---|
| SEBI-MC  | SEBI master circular on investor grievance redressal (sebi.gov.in -> Legal -> Master Circulars) |
| SEBI-MFR | SEBI (Mutual Funds) Regulations, investor servicing chapter (sebi.gov.in -> Legal -> Regulations) |
| RBI-IOS  | RBI Integrated Ombudsman Scheme (rbi.org.in -> the Scheme PDF) |

## File format the chunker understands

```
# doc_id: RBI-IOS
# regulator: RBI
# title: RBI Integrated Ombudsman Scheme
# version: 2021

[CLAUSE 2.1 | Heading text | kind: scope]
Clause text ...
```

`kind` is one of `scope`, `maintainability`, `servicing`, `general`. Cross references written
as `See clause 3.3` or `See clause OTHER-DOC#2.2` are detected and used by the agent.

## Using the real PDFs

1. Put the PDF in this folder. Run `python -m app.ingest --pdf-to-text corpus/file.pdf`
   to produce a `.txt` draft with page markers.
2. Edit the draft: add the header lines and `[CLAUSE ...]` markers around each numbered
   clause. 30-60 minutes per document; this manual step is the single most valuable
   thing you can do for retrieval quality, and it is the "chunking strategy that
   addresses the difficulty" for your report (L1).
3. `python -m app.ingest --dump` prints every chunk so you can check boundaries.
4. Re-derive the expected values in `eval/scenarios.json` from the real clauses.
