"""seqforge: prove the formulas OEIS still lists as conjectures.

OEIS holds thousands of "Conjecture:" and "Empirical:" lines -- recurrences,
generating functions and identities found by fitting terms, never proved. Each
one is a ready-made, falsifiable, infinite-family claim with an exact novelty
question (is a proof published?) and a place that takes the answer (an OEIS
comment). seqforge feeds them through mathforge's pipeline unchanged: hostile
search, independent re-check, proof, referee, second-model check, Lean 4.

    python seqforge.py A069429                 # one sequence
    python seqforge.py --auto 10 --workers 3   # the next ten unseen sequences
    python seqforge.py --forever --workers 3

Nothing is sent to OEIS: a machine-verified result gets a drafted comment in its
report.md for a person to submit. `--publish` uses mathforge's opt-in GitHub
publishing for machine-checked results.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
# mathforge's stages, Lean gate and AI wiring are reused, not copied
MATHFORGE = Path(os.getenv("MATHFORGE_DIR") or HERE.parent / "mathforge")
sys.path.insert(0, str(MATHFORGE))
import mathforge as mf  # noqa: E402

OUTPUT_ROOT = Path(os.getenv("SEQFORGE_OUTPUT") or HERE / "seq_output")
OEIS_SEARCH = "https://oeis.org/search?"
# Colin Barker's fitted generating functions on R. H. Hardin's array counts lead the
# first query: transfer-matrix objects, the most provable kind of OEIS conjecture
QUERIES = ('formula:"Empirical G.f."', 'formula:"Conjecture: a(n)"', 'formula:"Empirical: a(n)"')
MAX_PER_ENTRY = 4  # conjecture lines pursued per sequence
_OPEN = re.compile(r"(?i)\bconjectur|\bempiric")
# a settled line is not a target; the triage judge also sees the whole entry for the rest
_SETTLED = re.compile(r"(?i)\bprov(?:ed|en|es)\b|\bproof\b|\bcounterexample\b|\bfalse\b|\bfails\b")
_ASYMPTOTIC = re.compile(r"~|->|\blim\b|\bapprox")


def anum(entry: dict) -> str:
    return f"A{int(entry['number']):06d}"


def search(query: str, start: int = 0) -> list:
    """One page (10 entries) of an OEIS search, as its JSON records."""
    url = OEIS_SEARCH + urllib.parse.urlencode({"q": query, "fmt": "json", "start": start})
    return json.loads(mf._http_get(url)) or []


def entry(a: str) -> dict:
    got = search(f"id:{a}")
    if not got:
        raise SystemExit(f"{a}: no such OEIS sequence")
    return got[0]


def conjecture_lines(e: dict) -> list:
    """Open, exact claims: a conjecture or empirical formula, not asymptotic, not settled."""
    return [line for field in ("formula", "comment") for line in e.get(field) or []
            if _OPEN.search(line) and (("a(" in line and "=" in line) or "G.f." in line)
            and not _SETTLED.search(line) and not _ASYMPTOTIC.search(line)]


def targets(queries, done: set, limit: int) -> list:
    """Unseen sequences with at least one open line, query by query, page by page."""
    found, seen = [], set(done)
    for q in queries:
        start = 0
        while len(found) < limit:
            page = search(q, start)
            if not page:
                break
            for e in page:
                if anum(e) not in seen and conjecture_lines(e):
                    seen.add(anum(e))
                    found.append(e)
            start += len(page)
    return found[:limit]


class SeqForge(mf.Forge):
    def triage(self, e: dict, line: str) -> dict:
        """Turn one OEIS line into a mathforge conjecture, or reject it.

        On the review model, like mathforge's proposer: the prover then faces a
        statement it did not write.
        """
        text = "\n".join(e.get("formula") or []) + "\n" + "\n".join(e.get("comment") or [])
        return self.ask_json(
            f"OEIS {anum(e)}: {e.get('name', '')}\n"
            f"FIRST TERMS: {str(e.get('data', ''))[:300]}\n"
            f"FORMULA AND COMMENT LINES OF THE ENTRY:\n{text[:6000]}\n\n"
            f"TARGET LINE: {line}\n\n"
            "Decide whether the TARGET LINE is an open conjecture worth proving, and if so state it "
            "as a precise theorem. It is usable only if ALL hold:\n"
            "- it is an exact statement (identity, recurrence, generating function, divisibility, "
            "characterization) for every n in an infinite range, not an asymptotic, a numerical "
            "constant, or a bound on finitely many n;\n"
            "- a(n) can be computed from the sequence's DEFINITION (the name above, not the listed "
            "terms) by a brute-force program for enough n to test it in minutes;\n"
            "- no other line of the entry says it was proved or refuted;\n"
            "- it is not an immediate rewriting of the definition.\n\n"
            "The statement must be self-contained: define a(n) from the OEIS name in full, with its "
            "offset, so someone who never saw OEIS can test and prove it. For a generating function, "
            "state the equivalent linear recurrence with its initial terms as well.\n\n"
            'Return ONLY JSON: {"usable": true|false, "reason": "...", "title": "...", "headline": '
            '"the claim in at most 20 plain words", "statement": "...", "notation": "every symbol '
            'defined", "search_space": "the n range a brute-force check can cover, with bounds", '
            '"why_plausible": "the structure that suggests it (e.g. a transfer matrix)", '
            '"why_it_matters": "what a proof settles"}',
            "usable",
            model_type="review",
        )

    def novelty(self, c: dict) -> dict:
        """mathforge's retrieval + judge, asking the question that fits a listed conjecture."""
        return super().novelty({**c, "statement": (
            f"{c['statement']}\n\n(OEIS {c.get('oeis', '')} lists this as an unproved conjecture. The "
            "question is whether a published PROOF exists. Being listed as a conjecture does not make "
            "it known, and an easy proof is still worth writing down: count it KNOWN only if a proof is "
            "published or follows from a cited result.)")})


def done_ids() -> set:
    return {i["oeis"] for i in _index()}


def _index() -> list:
    path = OUTPUT_ROOT / "index.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _save_index(summary: dict) -> None:
    rows = [i for i in _index() if i["oeis"] != summary["oeis"]] + [summary]
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_ROOT / "index.json.tmp"
    tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, OUTPUT_ROOT / "index.json")


def report(e: dict, results: list, rejected: list) -> str:
    a = anum(e)
    out = [f"# {a}: {e.get('name', '')}", "", f"https://oeis.org/{a}", ""]
    for r in results:
        out += [f"## {r['id']}: `{r['status']}`", "", f"> {r['line']}", "", r.get("statement", ""), ""]
        if r.get("published"):
            out += [f"Published: {r['published']}", ""]
        if r["status"] == "machine-verified":
            where = r.get("published") or "<link to the published result>"
            out += ["Draft OEIS comment (submit by hand, after reading the proof and the Lean statement):", "",
                    f"    The conjecture \"{r['line'][:200]}\" is true; a proof, checked in Lean 4 + Mathlib, "
                    f"is at {where}.", ""]
        elif r["status"] == "machine-refuted":
            out += ["Counterexample, checked in Lean 4:", "", "```", mf._witness(r)[:1500], "```", ""]
    for line, why in rejected:
        out += [f"- not pursued: {line[:160]} -- {why}"]
    return "\n".join(out) + "\n"


def research(forge_for, e: dict, workers: int = 1, publish: bool = False) -> dict:
    """Every open line of one sequence, end to end. Returns the index summary."""
    a = anum(e)
    run = mf.Run(OUTPUT_ROOT / a)
    run.data.update(oeis=a, seed=f"OEIS {a}: {e.get('name', '')}")
    forge = forge_for(run)
    started = time.time()
    mf.rule(f"{a} {e.get('name', '')[:60]}")
    lines = conjecture_lines(e)[:MAX_PER_ENTRY]

    def triage(k_line):
        k, line = k_line
        return run.stage(f"triage{k}", lambda: forge.triage(e, line))

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(lines) or 1))) as pool:
        verdicts = list(pool.map(triage, enumerate(lines, 1)))
    conjectures, rejected = [], []
    for k, (line, t) in enumerate(zip(lines, verdicts), 1):
        if t.get("usable") and t.get("statement"):
            conjectures.append({**t, "id": f"c{k}", "oeis": a, "line": line})
        else:
            rejected.append((line, t.get("reason", "")))

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(conjectures) or 1))) as pool:
        results = list(pool.map(lambda c: mf.run_one(forge, c), conjectures))
    run.data["results"] = results
    run.save()
    if publish:
        mf.publish(forge, run.data["seed"], results)
        results = [{**r, "published": run.data.get(f"{r['id']}.published")} for r in results]

    tally = {}
    for r in results:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    if rejected:
        tally["unusable"] = len(rejected)
    (run.path / "report.md").write_text(report(e, results, rejected), encoding="utf-8")
    mf.log(f"{a} done in {mf._dur(time.time() - started)}: {tally}")
    summary = {"oeis": a, "name": e.get("name", ""), "tally": tally, "seconds": round(time.time() - started),
               "finished": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
    _save_index(summary)
    return summary


def setup_ai(args, state_file: Path):
    """mathforge's provider menu, model env vars and effort handling."""
    mf.load_local_env()
    interactive = sys.stdin.isatty() and not (args.model and args.review_model)
    _, config, picked = mf.choose_ai(args.provider, "review" if interactive else "auto", state_file=state_file,
                                     roles=("work", "review"), defaults=(mf.DEFAULT_MODEL, mf.DEFAULT_REVIEW_MODEL),
                                     default_provider="opencode-go")
    os.environ["AI_WRITING_MODEL"] = args.model or picked[0]
    os.environ["AI_REVIEW_MODEL"] = args.review_model or picked[-1]
    os.environ["AI_WRITING_COMPLETION_TOKENS"] = os.environ["AI_REVIEW_COMPLETION_TOKENS"] = str(mf.DEFAULT_MAX_TOKENS)
    ai = mf.AIService(config_path=config)
    effort, review = mf.resolve_efforts(None, None, interactive)
    mf.set_reasoning_effort(ai, effort, review)
    mf.log(f"models      {os.environ['AI_WRITING_MODEL']} (work) / {os.environ['AI_REVIEW_MODEL']} (review), "
           f"effort {effort} / {review}")
    return ai


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("anums", nargs="*", help="OEIS A-numbers to work on")
    ap.add_argument("--auto", type=int, metavar="N", help="pick N unseen sequences with open conjectures")
    ap.add_argument("--forever", action="store_true", help="keep picking sequences until interrupted")
    ap.add_argument("--workers", type=int, default=2, help="conjectures pursued in parallel per sequence")
    ap.add_argument("--provider")
    ap.add_argument("--model")
    ap.add_argument("--review-model")
    ap.add_argument("--no-lean", action="store_true", help="no Lean: nothing becomes machine-checked")
    ap.add_argument("--no-search", action="store_true", help="skip the literature search")
    ap.add_argument("--publish", action="store_true", help="mathforge's PUBLIC GitHub publishing of machine-checked results")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if not (args.anums or args.auto or args.forever):
        ap.error("give A-numbers, --auto N or --forever")
    mf.VERBOSE = mf.VERBOSE or args.verbose
    sys.stdout.reconfigure(line_buffering=True)
    mf.exit_on_ctrl_c(message="stopped; finished stages are cached, rerun to resume")

    lean = None
    if not args.no_lean:
        lean = mf.find_lean_project(None)
        if lean is None or (lean == mf.DEFAULT_LEAN_PROJECT and not (lean / mf.LEAN_READY).exists()):
            if mf.setup_lean(mf.DEFAULT_LEAN_PROJECT):
                ap.error("Lean setup failed; rerun to resume it, or pass --no-lean")
            lean = mf.DEFAULT_LEAN_PROJECT
    ai = setup_ai(args, OUTPUT_ROOT / "provider_state.json")
    forge_for = lambda run: SeqForge(ai, run, lean, search=not args.no_search)  # noqa: E731

    for a in args.anums:
        research(forge_for, entry(a.upper()), args.workers, args.publish)
    while args.auto or args.forever:
        batch = targets(QUERIES, done_ids(), args.auto or 5)
        if not batch:
            mf.log("no unseen sequences with open conjectures left in the queries")
            break
        for e in batch:
            try:
                research(forge_for, e, args.workers, args.publish)
            except Exception as exc:  # one bad sequence must not end a long session
                mf.log(f"{anum(e)} failed: {type(exc).__name__}: {exc}")
        if not args.forever:
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
