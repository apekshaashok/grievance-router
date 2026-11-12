# Incident log (D8)

D8 asks for at least one **genuine** entry recording a real failure during development. Entries INC-001..002 below are real
failures that occurred while this codebase was first built and tested (by the AI assistant that wrote it, in a sandbox). **You must
add your own entries from running and demonstrating the system on your machine** (a failed compose start, a rate-limit error from
your provider, a wrong routing you found). Do not copy these as if they were yours.

| ID | INC-001 |
|---|---|
| Date | 2026-10-25 (initial build) |
| Symptom | `test_baseline_prompt_fails_first_attempt_but_verifier_rescues` failed: the naive baseline prompt (v1) routed the section 4.1 FD case to RBI correctly on its first attempt (`attempts == 1`). |
| Detection | Automated test run. |
| Root cause | Wrong assumption, not a code bug: with the small illustrative sample corpus and the lexical hashing embedder, the RBI deposit clause outranks SEBI's text for the FD complaint, so even single-pass retrieval gets it right. |
| Fix | Test now uses a case the baseline really gets wrong on its first attempt (UPI failure). |
| Lesson / follow-up | **The 4.1 difficulty is a property of the real corpus and embedder, not guaranteed by this code.** At Evaluation 1, run the single-pass baseline (`PROMPT_VERSION=v1`) on the real corpus and report honestly whether it fails; the eval set's other scenarios show where the baseline does fail (S19, S21, S23 end wrong even after verification: grounded but wrong). |

| ID | INC-002 |
|---|---|
| Date | 2026-10-25 (initial build) |
| Symptom | First evaluation run: 1.49 million tokens per 100 runs; the `investigate` step was 96% of tokens. |
| Detection | Cost report at the end of `eval.run_eval`. |
| Root cause | Every model call re-sent every passage of every earlier tool call, including duplicates returned by overlapping searches. |
| Fix | Passages are de-duplicated into one `passages` map sent once per call; observations carry only passage ids (-12%, 1.30M tokens/100 runs). |
| Lesson / follow-up | The remaining cost is the growing context per step; see the report's cost section. Candidates: send only passages not yet seen plus a digest, cache across runs, or a smaller model for tool-selection steps. |

| ID | (yours) |
|---|---|
| Date | |
| Symptom | |
| Detection | |
| Root cause | |
| Fix | |
| Lesson / follow-up | |
