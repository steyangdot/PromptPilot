# Session Memory Verify-and-Repair Plan Review (Fresh Pass)

## Summary
I re-reviewed `SESSION_MEMORY_VERIFY_REPAIR.md` as a standalone pass and found several correctness risks that are important before implementation, mainly around how execution verification is actually enforced.

## Findings

1. High: Probe example in the plan may miss the failure mode it claims to catch  
The design correctly states probes must execute runtime behavior, but the sample C1 probe only calls `build_request(...)` and inspects `extensions` instead of issuing a real request. This can pass while runtime merge/regression errors still exist. This weakens the central claim that execution probes catch behavior regressions, because the example probe does not exercise the same path that produced the documented run9 failure.

2. High: “Probe-validated-at-birth” can be over-specified as proof of detection  
Validating a newly generated probe only once against the same tree proves it was generated correctly for current code, not that it is necessarily a sensitive regression detector over time. Without explicit negative-case or mutation/fixture checks, this can give false confidence.

3. High: `unknown` outcomes are not equivalent to clean outcomes  
`unknown` is marked as fail-closed in text, but if unknown is still aggregated as “no violation,” correctness claims can be overstated. For reporting and gating, unknown should be treated as unverified/censored rather than equivalent to clean.

4. Medium: Over-reliance on overlap filtering can miss semantically broken contracts  
`verify_contracts` only checks prior contracts with overlapping `files/symbols` against current changes. Behavior can be broken indirectly via shared plumbing, dependency imports, and unrelated file edits. For late-run verification, this can under-detect failures.

5. Medium: Repair path does not define ledger/diff reconciliation after repair turn  
The pseudocode captures the pre-memory snapshot, runs the turn, verifies, and may run one repair. It does not specify whether changed files, evidence, or ledger state are refreshed after repair. That leaves ambiguity in what constitutes the final accepted tree.

6. Medium: Executing SLM-generated snippets needs a stricter runtime contract  
The plan blocks network via MockTransport and timeouts, but generated probes are still code execution. Even without network calls they can still misbehave unless runner constraints are explicit (environment lockdown, read-only workspace assumptions, restricted side effects).

## Open questions

- Should `unknown` count as blocking for final pass/fail in A1 vs B comparisons?  
- Should final-turn verification run all prior contracts (not just overlap-filtered ones)?  
- Should we add an explicit “golden-failing” corpus for probe quality before production rollout?  
- Which runner sandbox guarantees (env, cwd writeability, module import boundaries) are mandatory for probe execution?

## Suggested next edits before implementation

1. Replace the C1 probe example with a real execution call (`client.get(...)`) that reproduces the merge-path.  
2. Clarify `unknown` metrics and gating: unknown should be reported as unverified and handled conservatively.  
3. Add repair-state refresh and post-repair verification artifacts (diff scope and evidence) in the run chain path.  
4. Define probe execution hardening policy (filesystem/network/process side-effect limits) as part of the contract.

