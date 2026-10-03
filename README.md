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
python seqforge.py A069429 --effort low --review-effort high
python -B -m unittest -q test_seqforge       # offline checks
```

Provider and models come from the shared ai-suite menu. Picks are remembered in
`seq_output/provider_state.json`. Lean uses mathforge's `~/mathforge-lean`;
`--no-lean` skips it. `--provider`, `--model` and `--review-model` override the
shared menu. `--effort` and `--review-effort` override its per-role reasoning picks;
`provider-default` sends no effort override. Without flags, menu/environment picks
and mathforge's unattended defaults are unchanged. Rerun an A-number to resume its
cached stages; SeqForge has no `--resume` flag.

Output goes to `seq_output/<A-number>/`, which holds `state.json` (every stage,
resumable by rerunning the A-number), the generated scripts and `report.md`.
`seq_output/index.json` records every sequence done.

## Publishing

`--publish` pushes each `machine-verified` and `machine-refuted` result to the public
GitHub repository `<you>/seqforge-results` (`SEQFORGE_RESULTS_REPO`, checkout
`~/seqforge-results` or `SEQFORGE_RESULTS_DIR`). It uses mathforge's publishing code:
one folder per result with the write-up, the scripts, the Lean file and a Palomar
bundle. `--publish-existing` publishes what is already on disk and exits.

Each published folder also shows the conjecture line as the OEIS lists it, with the
agreement script and its output. Nothing is sent to the OEIS automatically. The
local `seq_output/<A-number>/report.md` holds a drafted comment and the link to the
sequence's edit form. A person submits it through an OEIS account, after reading the
proof and checking the Lean statement against the conjecture. This stays manual: the
OEIS [forbids](https://oeis.org/wiki/Use_of_AI_for_OEIS_Submissions_is_Forbidden)
automated submissions and comments written in full by a model, and blocks accounts
that repeat them.
