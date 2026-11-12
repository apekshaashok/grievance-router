# Incident log (D8)

Real failures met while building and running the system. INC-001 and INC-002 occurred during the first build on the
illustrative sample corpus; INC-003 onwards occurred on 2026-11-12 while moving to the real corpus, a real model and Docker.
Add an entry for every failure you hit while demonstrating.

| ID | INC-001 |
|---|---|
| Date | 2026-10-25 (first build, sample corpus) |
| Symptom | `test_baseline_prompt_fails_first_attempt_but_verifier_rescues` failed: the single-pass baseline (prompt v1) routed the section 4.1 case correctly on its first attempt. |
| Detection | Automated test run. |
| Root cause | Wrong assumption: with a tiny sample corpus and the lexical hash embedder, the RBI deposit clause outranked SEBI's text, so even one retrieval pass was right. |
| Fix | Test switched to a case the baseline gets wrong. |
| Follow-up (resolved 2026-11-12) | On the real corpus with MiniLM embeddings the difficulty is real: the single pass returns six SEBI passages and no RBI clause (`docs/eval1_reference_case.md`). |

| ID | INC-002 |
|---|---|
| Date | 2026-10-25 (first build) |
| Symptom | 1.49 M tokens per 100 runs; `investigate` was 96% of them. |
| Detection | Cost summary printed by `eval.run_eval`. |
| Root cause | Each model call re-sent every passage of every earlier tool call, duplicates included. |
| Fix | Passages de-duplicated into one map sent once per call; observations carry only ids (-12%). |
| Follow-up | Context still grows per step; on the real corpus `investigate` is still ~97% of tokens (see report, cost). |

| ID | INC-003 |
|---|---|
| Date | 2026-11-12 |
| Symptom | First run against a real model (`llama3.2` 3B via Ollama): the section 4.1 case ended `escalate-to-human / limits_exceeded` after 10 steps; nothing was routed. |
| Detection | Trace of the run (`one.py S01`): the model replied `{"action":"assign-to-entity","tool":"list_clauses",...}` (route name in the action field, rejected by the parser), then repeated the identical `list_clauses` call seven times. |
| Root cause | A 3B model does not follow a two-level JSON protocol reliably; the loop had no defence against repeating a call, only the step bound. The bound worked as designed: no answer was returned. |
| Fix | (1) A repeated identical tool call is answered with a "duplicate call" observation instead of being re-run. (2) With two steps left the model is told to finish or escalate. (3) The format error names the fix ("action is tool or finish; the route goes in determination.route"). (4) The prompt shows one example of each reply. Regression test `test_repeated_tool_call_is_not_rerun_and_run_still_ends`. With these, llama3.2 stopped looping but then copied the enum template literally (`"route":"refer-to-other-regulator|not-maintainable|..."`); the Verifier rejected it and the run escalated. |
| Lesson | Use an 8B-class model or larger (`gemma4` works). Loop bounds are what makes a weak model safe, not what makes it useful. |

| ID | INC-004 |
|---|---|
| Date | 2026-11-12 |
| Symptom | With `gemma4` the 4.1 run escalated `provider_unavailable` although Ollama was up. The last two model replies had `completion_tokens` > 0 and empty content, at ~3,900 prompt tokens. |
| Detection | Trace: `LLM investigate pt=3894 ct=202 :: ''`. |
| Root cause | Ollama's OpenAI-compatible endpoint uses the model's default context window (4,096 tokens); the agent's context grows with each tool result, so the prompt was silently truncated. Two unparseable replies triggered the format fallback, and with `LLM_FALLBACK=none` the run escalated, correctly. |
| Fix | A local model alias with a 16k context: `ollama create gemma4-16k -f Modelfile` (`FROM gemma4`, `PARAMETER num_ctx 16384`). Documented in the README. Also raised `LLM_MAX_TOKENS` to 2000 (now configurable): an earlier reply had been cut at the old fixed 1,200-token limit because the model copied whole passages into its claims; the prompt now asks for short one-sentence claims. |
| Lesson | Check the provider's context limit against the largest prompt in a trace before an evaluation. |

| ID | INC-005 |
|---|---|
| Date | 2026-11-12 |
| Symptom | First evaluation on the real corpus: accuracy 0.79; every RBI referral (S01-S05, S25, S29) escalated `verification_failed`. |
| Detection | `eval.run_eval -v`; trace showed the issue "output makes a disposal-type statement". |
| Root cause | The output filter's pattern `is ... rejected` matched the advice "approach the Ombudsman **if it is rejected**", a condition, not a disposal. The sample corpus had not exercised this wording. |
| Fix | A disposal phrase preceded by a conditional (if, unless, when, where, whether, once, until) in the same clause is not blocked. Test `test_conditional_wording_is_not_a_disposal`. |

| ID | INC-006 |
|---|---|
| Date | 2026-11-12 |
| Symptom | The 4.1 referral cited `RBI-IOS#3(1)(l)` (definition of System Participant) as the clause showing a bank is covered. |
| Detection | Reading the determination's basis. The Verifier accepted it: the clause is an RBI scope clause and the claim is copied verbatim (grounded but not the best evidence). |
| Root cause | Evidence selection matched the term "bank", which also occurs in "Reserve **Bank**" inside that definition. |
| Fix | Entity terms for a bank are now "banking company" / "co-operative bank". |
| Lesson | The Verifier checks that a citation is true and of the right kind, not that it is the most relevant one. |

| ID | INC-007 |
|---|---|
| Date | 2026-11-12 |
| Symptom | Mutual-fund complaints could not take their timeline from MF Regulation 60A(1). |
| Root cause | The timeline parser matched only "within N days" with digits; the regulation says "not later than **twenty-one** calendar days". |
| Fix | `app/agents/timeline.py` parses digits and number words and both phrasings; shared by the rule engine and the Verifier. Test `test_timelines_written_in_words_and_digits`. |

| ID | INC-008 |
|---|---|
| Date | 2026-11-12 |
| Symptom | Local evaluation with sentence-transformers aborted at start: `OMP: Error #15 ... multiple copies of the OpenMP runtime`. |
| Root cause | On macOS, faiss-cpu and torch wheels each ship their own OpenMP runtime. Does not occur in the Linux containers. |
| Fix | Local runs on macOS set `KMP_DUPLICATE_LIB_OK=TRUE` (documented in the README); containers unaffected. |

| ID | INC-009 |
|---|---|
| Date | 2026-11-12 |
| Symptom | First `docker compose build` was still downloading Python wheels after several minutes (~130-190 kB/s inside the build, much faster on the host). |
| Detection | `--progress plain` build log. |
| Root cause | Network throughput of the Docker Desktop VM on this machine; torch (CPU) is the largest download. |
| Fix | None needed for correctness. CPU-only torch wheels (`download.pytorch.org/whl/cpu`) keep the download smaller than the default CUDA build. Build once before the demonstration; `INSTALL_ST=0` builds a smaller image that uses the hash embedder. |

| ID | INC-010 |
|---|---|
| Date | 2026-11-12 |
| Symptom | Real-model runs (`gemma4`) that clearly reached the right answer ended `provider_unavailable`; model calls were slow and a 10-word verifier reply reported ~950 completion tokens. |
| Detection | A spy on the JSON parser printed the raw replies: valid-looking determinations missing their final `}`; a direct request showed a hidden `reasoning` field consuming the output budget. |
| Root cause | (1) `gemma4` is a thinking model: on Ollama it reasons before answering unless told not to, spending time and `max_tokens`. (2) It regularly drops the last closing brace of a nested JSON object. Two unparseable replies trigger the format fallback; with `LLM_FALLBACK=none` the run escalates. |
| Fix | `LLM_REASONING_EFFORT=none` (new setting, sent only when set). The parser now appends missing closing brackets when that alone makes the text valid JSON (never rewrites content; result still schema-checked and verified). Test `test_json_missing_final_brace_is_repaired_but_garbage_is_not`. |

| ID | INC-011 |
|---|---|
| Date | 2026-11-12 |
| Symptom | First full real-model evaluation: scenarios S15-S34 all escalated `provider_unavailable` in ~1 ms each; S07 took 1,995 s although `RUN_TIMEOUT_S=900`. Accuracy 0.03, meaningless. |
| Detection | Per-scenario results (`exec_s`, escalation reasons). |
| Root cause | The Locust load tests were running in Docker at the same time; local inference slowed, calls timed out (each up to 240 s, retried twice), the circuit breaker opened, and the next scenarios started within the 20 s cooldown, so the breaker refused them, correctly. Separately, the run deadline was only checked between steps, so one call with retries could overrun it. |
| Fix | (1) Model calls now get `min(LLM_TIMEOUT_S, time left in the run)` and no retry starts after the deadline. (2) The eval harness waits out the breaker and reruns a scenario that ended `provider_unavailable` (at most twice; reported as `provider_reruns`). (3) Real-model evaluations are run with nothing else loading the machine. |
| Lesson | An outage must never be scored as a quality result, and the breaker is shared by every run in a process: one slow period affects the runs after it. |
