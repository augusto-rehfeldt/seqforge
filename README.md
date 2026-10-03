# seqforge

Proves, or refutes, the formulas the OEIS still lists as conjectures.

The OEIS holds thousands of `Conjecture:` and `Empirical:` lines: recurrences,
generating functions and identities fitted to the listed terms and never proved
(`formula:conjecture` matched 7620 sequences on 2026-10-02). Each is a ready-made
claim about an infinite family, testable by brute force. It also comes with a
precise novelty question (has anyone published a proof?) and a place that takes
the answer (an OEIS comment).

seqforge reuses [mathforge](../mathforge/README.md)'s pipeline unchanged: hostile
counterexample search, independent re-check, proof, referee, second-model check,
Lean 4 + Mathlib, and a back-translation of the Lean statement. It adds three
things:

- **Selection.** `conjecture_lines` keeps open, exact claims. It drops asymptotic
  claims and any line that says it was proved or refuted. `targets` pages through
  `QUERIES`, skipping sequences already done.
- **Triage.** On the review model, `SeqForge.triage` sees the whole entry, rejects
  conjectures that another line settles, and restates each usable one as a
  self-contained theorem.
- **Novelty.** mathforge's retrieval and judge, asked whether a *proof* exists.
  Being listed as a conjecture does not make a claim known.

## Usage

```bash
python seqforge.py A069429                   # one sequence
python seqforge.py --auto 10 --workers 3     # the next ten unseen sequences
python seqforge.py --forever --workers 3
python -B -m unittest -q test_seqforge       # offline checks
```

Provider and models come from the shared ai-suite menu. Picks are remembered in
`seq_output/provider_state.json`. Lean uses mathforge's `~/mathforge-lean`;
`--no-lean` skips it.

Output goes to `seq_output/<A-number>/`, which holds `state.json` (every stage,
resumable by rerunning the A-number), the generated scripts and `report.md`.
`seq_output/index.json` records every sequence done.

## Publishing

Nothing is sent to the OEIS. A `machine-verified` result gets a drafted comment in
its `report.md`, for a person to read and submit. `--publish` turns on mathforge's
opt-in GitHub publishing for machine-checked results.
