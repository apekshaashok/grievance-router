# Evaluation 1: the section 4.1 reference case

> "I invested in a fixed deposit through my bank's app. The interest credited is lower than what I was promised.
> I want to complain to SEBI."

## Values tested

Request (`POST /v1/complaints`, also `scripts/demo.py fd` and eval scenario `S01-fd-bank-app`):

```json
{"investor_ref": "INV100001", "channel": "web", "complaint_date": "2026-10-20",
 "complaint_text": "I invested in a fixed deposit through my bank's app. The interest credited is lower than what I was promised. I want to complain to SEBI.",
 "amount_inr": 200000, "entity_name": "Example Bank"}
```

Reference answer (what the demonstration must produce):

| Field | Value |
|---|---|
| `route` | `refer-to-other-regulator` |
| `regulator` | `RBI` (Reserve Bank - Integrated Ombudsman Scheme, 2021) |
| `domain` | `out_of_jurisdiction` |
| Must cite | `SEBI-SCORES#AII-5.3` (SEBI's system does not deal with RBI matters) **and** an RBI clause showing the Scheme covers a bank: `RBI-IOS#3(1)(e)` or `RBI-IOS#3(1)(j)`, with `RBI-IOS#9` / `RBI-IOS#8(1)` for the grounds |
| Next step | first a written complaint to the bank; the Ombudsman if rejected or unanswered for 30 days (`RBI-IOS#10(2)(a)`) |
| Must not | assign it to a SEBI entity, give a SEBI 21-day timeline, or state that the complaint is resolved/rejected |

Variants in the evaluation set: `S25` (same complaint with a prompt-injection sentence appended), `S33` (bank FD, the
investor already wrote to the bank and had no reply for 45 days).

## The passages the case depends on (verbatim from the index)

**Why SEBI's framework does not take it.** SEBI's text lists what SCORES covers; only its exclusions say what it does not.

* `SEBI-SCORES#P1` (scope): "... the aggrieved investors, whose grievances against (a) listed company, (b) registered
  intermediary or (c) market infrastructure institution ("Entities") remain unresolved." A bank is none of these.
* `SEBI-SCORES#AII-5.3` (maintainability, **the modifying provision**): "The following types of complaints shall not be
  dealt through SCORES: Complaints falling under the purview of other regulatory bodies such as Reserve Bank of India,
  (RBI), Insurance Regulatory and Development Authority of India (IRDAI), Pension Fund Regulatory and Development Authority
  of India (PFRDAI), Competition Commission of India (CCI), or complaints falling under the purview of other ministries."

**Why the RBI Scheme does.** The Scheme never mentions "fixed deposit"; the case is decided through its definitions.

* `RBI-IOS#3(1)(e)`: ""bank" means a 'banking company', a 'corresponding new bank', a 'Regional Rural Bank', 'State Bank of India' ..."
* `RBI-IOS#3(1)(j)`: ""Regulated Entity" means a bank or a Non-Banking Financial Company, or a System Participant or a Credit Information Company ..."
* `RBI-IOS#9`: "Any customer aggrieved by an act or omission of a Regulated Entity resulting in deficiency in service may file a complaint under the Scheme ..."
* `RBI-IOS#8(1)`: "The Ombudsman/Deputy Ombudsman shall consider the complaints of customers of Regulated Entities relating to deficiency in service."
* `RBI-IOS#10(2)(a)`: "A complaint under the Scheme shall not lie unless the complainant had, before making a complaint under the Scheme, made a
  written complaint to the Regulated Entity concerned and ... the complainant had not received any reply within 30 days ..."

## Why one retrieval pass fails here (measured)

Single search with the complaint text, `all-MiniLM-L6-v2` embeddings, clause-level chunks, 64 chunks:

| rank | chunk | heading |
|---|---|---|
| 1 | SEBI-SCORES#AII-10 | ODR or civil remedy while pending on SCORES |
| 2 | SEBI-SCORES#P5.1 | Action Taken Report to SEBI |
| 3 | SEBI-SCORES#AI-1.2 | Entity to resolve within 21 calendar days |
| 4 | SEBI-SCORES#AI-3.1 | Second review by SEBI |
| 5 | SEBI-SCORES#AI-1.4 | Copy to the Designated Body |
| 6 | SEBI-SCORES#AII-4 | Complaints beyond the limitation period |
| 11 | **SEBI-SCORES#AII-5.3** | the exclusion that decides the case |

All top-6 passages are SEBI's grievance framework: "complain to SEBI" and "invested" pull the search there, exactly as the
brief predicts. A pipeline that answers from these passages assigns the complaint to a SEBI entity with a 21-day timeline.
(With the lexical `hash` embedder the RBI definitions happen to rank first, because "bank", "deposit" and "interest" match
literally; the difficulty depends on the embedder, so it is stated here for the configuration the containers use.)

## How the agent gets it right

1. `search_corpus` with the complaint text: the provision that appears to apply (SEBI SCORES).
2. The complainant named SEBI: `list_clauses regulator=SEBI kind=maintainability` asks whether SEBI's framework excludes it,
   and returns `AII-5.3`.
3. A query derived from what was read ("bank regulated entity deficiency in service ... fixed deposit"), filtered to RBI,
   then `list_clauses regulator=RBI kind=scope` and `kind=maintainability`: `3(1)(e)`, `3(1)(j)`, `9`, `8(1)`, `10(2)(a)`.
4. The Verifier checks every citation against retrieved text, and enforces that a complaint addressed to SEBI and referred
   to RBI cites the SEBI exclusion.
5. If that check fails, the Investigator retries with the Verifier's issue list. Observed with a real model (`gemma4`,
   8B, local): its first answer cited only `RBI-IOS#3(1)(e)`; the Verifier rejected it ("cite the SEBI maintainability
   clause that excludes matters within the RBI framework"); the model searched SEBI maintainability clauses, found
   `AII-5.3`, and the second answer passed. The trace is in `docs/evidence/`.
