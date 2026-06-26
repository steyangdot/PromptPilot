# Session Memory Verify-and-Repair Methodology and Clarity Review

## Summary
This review focuses on the research methodology, claims, experimental framing, and readability of `SESSION_MEMORY_VERIFY_REPAIR.md`. The plan has a strong central insight: execution feedback is needed because prompt/ledger recall cannot guarantee implementation correctness. The main improvements are to tighten the success criteria, pre-register outcome handling, separate measurement from intervention, and make the document easier to audit.

## Methodology Findings

1. High: The A1 vs B success criterion is underpowered as written  
The plan proposes `with_memory_verify` (B) vs `with_memory` (A1) on `chain_long N=5`. Given the observed A1 residual tax is 1/10 in the latest run and 0/5 in the prior snapshot, an N=5 comparison is very likely to be dominated by variance. "B's residual tax < A1's" may be impossible to show if A1 happens to produce 0/5 again. The gate should allow a paired or fixture-based success claim, or increase N / aggregate across multiple seeds.

2. High: Detection and repair should be separated in reported claims  
The plan correctly says detection is measurement and repair is the lever, but the proposed B arm combines both. That makes it harder to know whether the new mechanism improves outcomes because it detects more failures, repairs detected failures, or simply adds another agent turn. Consider reporting at least three layers: verifier-only detection accuracy, one-turn repair success on detected failures, and end-to-end B outcome.

3. High: Handling of `unknown`/censored outcomes needs pre-registration  
The plan mentions `unknown`, malformed probes, timeouts, and no-match outcomes, but does not define how those affect tax rate, verifier precision/recall, or B-vs-A1 success. Without a pre-registered rule, the analysis can accidentally drift toward optimistic accounting. Unknown should be counted separately from clean and should probably censor or block clean-pass claims for affected contracts.

4. Medium: "Flags run9 only" is too narrow as a Phase 1 gate  
The verifier should definitely catch run9, but "nothing else" assumes the existing oracle is complete. If the verifier exposes another real execution failure, that should be adjudicated, not automatically treated as a false positive. A stronger gate is: catch run9, produce no unsupported violations, and manually adjudicate any additional execution-backed failures.

5. Medium: The plan needs a paired-comparison story  
The current A1 vs B design can be confounded by run-to-run variance, model drift, benchmark setup drift, and random agent behavior. A clearer methodology would pair runs by seed/input/tool version where possible, or replay the same finished runs through verifier-only detection before spending on live repair arms.

6. Medium: The treatment effect should be stated as "verify+repair" rather than "memory"  
The honest positioning section is already careful, but some earlier wording still risks implying that the memory layer itself improves correctness. The clean claim is narrower and stronger: memory supplies obligations; execution verifies them; repair attempts to restore them.

7. Low: Token-cost accounting needs to include repair and latency separately  
The plan says verification is near-zero model tokens, which is true for subprocess probes, but the B arm includes repair turns. The reporting section should keep probe latency, probe execution cost, SLM probe-generation cost, and repair-turn model tokens separate so the cost story remains auditable.

## Clarity Findings

1. High: The canonical probe example conflicts with the document's key lesson  
The document says probes must exercise behavior, not merely construct objects, but the example probe inspects `build_request(...).extensions`. This creates reader confusion and risks implementers copying a weak pattern. Replace the example with one that actually performs `client.get(...)` using `MockTransport`.

2. Medium: Several terms need stricter definitions before implementation  
Terms like "high-confidence violation," "gated," "guard_hits," "at-risk contract," "bounded diff," and "unknown" are understandable in context, but not precise enough for implementers. A short "Operational Definitions" section would prevent accidental divergence.

3. Medium: The phase table mixes validation gates and implementation milestones  
Some gates are empirical claims, some are implementation checks, and some are benchmark outcomes. Splitting each phase into "artifact," "local acceptance," and "research claim" would make the rollout easier to execute and review.

4. Medium: The doc would benefit from an explicit data schema section  
The ledger schema adds `probe`, but the result schema, evidence schema, repair metrics, and unknown/censored fields are distributed across sections. A compact schema block for `ContractProbe`, `VerifierResult`, and `RunVerifierMetrics` would make the plan much easier to implement faithfully.

5. Low: Encoding artifacts reduce readability and trust  
The document contains mojibake in arrows, section references, multiplication signs, and ranges. This is not conceptually harmful, but it makes the plan look less stable and can interfere with search, copy/paste, or downstream parsing.

## Recommended Edits

1. Replace the probe example with a runtime `client.get(...)` probe.
2. Add a pre-registered outcomes section: clean, violated, unknown, censored, repaired, unrepaired.
3. Split reporting into verifier-only, repair-only, and end-to-end arm metrics.
4. Adjust Phase 1 from "flags run9 only" to "flags run9; extra findings are adjudicated against execution evidence."
5. Reframe Phase 4 as either paired runs, higher N, or fixture-backed acceptance plus a smaller live smoke run.
6. Add an operational definitions block for implementation-sensitive terms.
7. Fix encoding artifacts before using this as an implementation source document.

