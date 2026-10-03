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
import hashlib
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
MIN_FORMULA_TERMS = 8  # listed terms both formulas must reproduce
MIN_DEFINITION_TERMS = 5  # listed terms a brute force of the restated definition must reproduce
_AGREE = re.compile(r"TERMS AGREE:\s*formulas\s*=\s*(\d+)[,;\s]+definition\s*=\s*(\d+)")
_OPEN = re.compile(r"(?i)\bconjectur|\bempiric")
# a settled line is not a target; the triage judge also sees the whole entry for the rest
_SETTLED = re.compile(r"(?i)\bprov(?:ed|en|es)\b|\bproof\b|\bcounterexample\b|\bfalse\b|\bfails\b|\btrue\b|\bcorrect\b")
_ASYMPTOTIC = re.compile(r"(?i)~|->|\blim\b|\bapprox|asymptotic")
_GF = re.compile(r"(?i)\bg\.f\.")  # Barker writes "Empirical g.f.", Hardin "Empirical G.f."
# an entry whose own text settles its conjectures has no target, whichever line it means
_ENTRY_SETTLED = re.compile(r"(?i)conjectures\s+(?:above\s+)?are\s+(?:true|correct)"
                            r"|(?:conjecture\s+above|above\s+conjectures?)\s+(?:is|are)\s+(?:true|correct)"
                            r"|formulas?\s+(?:above\s+)?(?:is|are)\s+correct"
                            r"|conjectur\w*\s+(?:above\s+)?(?:was|were|has been|have been)\s+proved")
_SIGNATURE = re.compile(r"\s+-\s+_[^_]+_.*$")  # "... - _Colin Barker_, Feb 22 2012"


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
    lines = [line for field in ("formula", "comment") for line in e.get(field) or []]
    if _ENTRY_SETTLED.search("\n".join(lines)):
        return []
    return [line for line in dict.fromkeys(lines)
            if _OPEN.search(line) and (("a(" in line and "=" in line) or _GF.search(line))
            and not _SETTLED.search(line) and not _ASYMPTOTIC.search(line)]


def _key(line: str) -> str:
    """Stage ids from the line's text: OEIS entries change between runs, positions shift."""
    # decimal digits: mathforge tags log lines by `c<digits>`
    return f"{int(hashlib.sha1(line.encode('utf-8')).hexdigest(), 16) % 10**7:07d}"


def terms_agree(check: dict) -> bool | None:
    """True / False from the agreement script's verdict, None when it gave none or compared
    too few terms to mean anything."""
    out = check.get("output", "")
    if "TERMS DIFFER" in out:
        return False
    got = _AGREE.search(out) if check.get("exit_code") == 0 else None
    if not got:
        return None
    return True if int(got[1]) >= MIN_FORMULA_TERMS and int(got[2]) >= MIN_DEFINITION_TERMS else None


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
            f"OFFSET: {e.get('offset', '')}\nFIRST TERMS: {str(e.get('data', ''))[:300]}\n"
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
            "- it is not an immediate rewriting of the definition;\n"
            "- it is not a famous open problem or a case of one (prime gaps, irreducibility patterns "
            "of primes, Goldbach-type statements): those will not be settled here.\n\n"
            "The statement must be self-contained: define a(n) from the OEIS name in full, with its "
            "offset, so someone who never saw OEIS can test and prove it. For a generating function, "
            "state the equivalent linear recurrence with its initial terms as well.\n\n"
            "The search space must test well past what the claim's own size determines: at least "
            "twice the recurrence order plus ten, or the polynomial degree plus ten, and n <= 30 or "
            "more wherever brute force allows. A degree-8 polynomial checked at four points proves "
            "nothing.\n\n"
            'Return ONLY JSON: {"usable": true|false, "reason": "...", "title": "...", "headline": '
            '"the claim in at most 20 plain words", "statement": "...", "notation": "every symbol '
            'defined", "search_space": "the n range a brute-force check can cover, with bounds", '
            '"why_plausible": "the structure that suggests it (e.g. a transfer matrix)", '
            '"why_it_matters": "what a proof settles"}',
            "usable",
            model_type="review",
        )

    def agree(self, e: dict, c: dict) -> dict:
        """Executable gate between the OEIS line and its restatement: triage is one model's
        rewrite (offset, initial terms, G.f. to recurrence), and every later stage checks
        the rewrite, not the line. On the work model, the other one."""
        return self.write_and_run(
            "Check that a restated theorem says the same thing as an OEIS formula, on the listed terms.\n\n"
            f"OEIS {anum(e)}: {e.get('name', '')}\nOFFSET: {e.get('offset', '')}\n"
            f"LISTED TERMS a(offset), a(offset+1), ...: {e.get('data', '')}\n\n"
            f"OEIS LINE: {c['line']}\n\nRESTATED THEOREM: {c['statement']}\nNOTATION: {c.get('notation', '')}\n\n"
            "Write ONE self-contained Python 3 script (stdlib; fractions for exact arithmetic) that:\n"
            "- computes terms from the OEIS LINE exactly as written (expand a generating function as an "
            "exact power series; iterate a recurrence from the listed initial terms; evaluate a closed "
            "form), and compares them with the listed terms at the right offset;\n"
            "- computes terms from the RESTATED THEOREM's formula the same way and compares them too;\n"
            "- separately, brute-forces a(n) from the RESTATED definition (in the NOTATION, not from "
            "any formula: enumerate the objects and count) for as many small n as fit in two minutes, "
            f"at least {MIN_DEFINITION_TERMS}, and compares those with the listed terms too;\n"
            f"- compares at least {MIN_FORMULA_TERMS} listed terms for the formulas;\n"
            "- prints exactly `TERMS AGREE: formulas=<k> definition=<m>` (k, m = listed terms each check "
            "matched) when everything matches, else `TERMS DIFFER:` with which check, the first index "
            "and the values.\n"
            "Exit code 0 either way; no bare `assert`. Return only the script in one ```python fence.",
            f"{c['id']}_agree",
            markers=("TERMS AGREE", "TERMS DIFFER"),
        )

    def novelty(self, c: dict) -> dict:
        """mathforge's retrieval and judge on the plain statement, then a second judge that asks
        the question a listed conjecture raises, with the OEIS context mathforge does not see."""
        base = super().novelty(c)
        cross = []
        if self.search:
            try:  # proofs are often written into the entries that cite this one
                for x in search(c.get("oeis", ""))[:8]:
                    if anum(x) != c.get("oeis"):
                        mentions = [ln for ln in (x.get("formula") or []) + (x.get("comment") or [])
                                    if c.get("oeis", "?") in ln]
                        cross.append(f"{anum(x)}: {x.get('name', '')}\n  " + "\n  ".join(mentions[:4]))
            except Exception as exc:
                cross.append(f"(OEIS cross-reference search failed: {type(exc).__name__})")
        retrieved = "\n".join(f"- {h.get('title', '')} {h.get('url', '')}" for h in base.get("retrieved") or [])
        mine = self.ask_json(
            f"OEIS {c.get('oeis', '')} lists this as an unproved conjecture:\n{c.get('line', '')}\n\n"
            f"RESTATED: {c['statement']}\n\n"
            f"REFERENCES AND LINKS OF THE ENTRY:\n{c.get('oeis_refs', '') or '(none)'}\n\n"
            f"OTHER OEIS ENTRIES THAT CITE IT:\n{chr(10).join(cross) or '(none retrieved)'}\n\n"
            f"A GENERIC NOVELTY JUDGE SAID: {base.get('verdict')} -- {base.get('reasoning', '')}\n"
            f"CLOSEST KNOWN: {base.get('closest_known_results')}\nLITERATURE RETRIEVED:\n{retrieved or '(none)'}\n\n"
            "The question is whether a published PROOF exists. Being listed as a conjecture does not "
            "make it known, and an easy proof is still worth writing down for OEIS. Say KNOWN only if "
            "a proof is published, or follows directly from a result cited above (name it); "
            "APPARENTLY_NEW if not; UNCLEAR if a source above may contain it.\n\n"
            'Return ONLY JSON: {"verdict": "KNOWN"|"UNCLEAR"|"APPARENTLY_NEW", "reasoning": "...", '
            '"matching_hits": ["the source that proves it, if any"]}',
            "verdict",
            model_type="review",
        )
        return {**base, "generic_verdict": base.get("verdict"), "verdict": mine.get("verdict"),
                "reasoning": mine.get("reasoning", ""), "oeis_cross_references": cross,
                "matching_hits": list(base.get("matching_hits") or []) + list(mine.get("matching_hits") or [])}


def done_ids() -> set:
    return {i["oeis"] for i in _index()}


def _index() -> list:
    path = OUTPUT_ROOT / "index.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:  # kept for a person to look at, never silently reset
        path.rename(path.with_name(f"index.damaged-{int(time.time())}.json"))
        return []


def _save_index(summary: dict) -> None:
    rows = [i for i in _index() if i["oeis"] != summary["oeis"]] + [summary]
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_ROOT / "index.json.tmp"
    tmp.write_text(json.dumps(rows, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, OUTPUT_ROOT / "index.json")


def _url(published) -> str | None:
    return published.get("url") if isinstance(published, dict) else published


def report(e: dict, results: list, rejected: list) -> str:
    a = anum(e)
    out = [f"# {a}: {e.get('name', '')}", "", f"https://oeis.org/{a}", ""]
    for r in results:
        out += [f"## {r['id']}: `{r['status']}`", "", f"> {r['line']}", "", r.get("statement", ""), ""]
        url = _url(r.get("published"))
        if url:
            out += [f"Published: {url}", ""]
        if r["status"] == "machine-verified":
            quoted = _SIGNATURE.sub("", r["line"])[:300]
            out += ["Draft OEIS comment (submit by hand, after reading the proof and the Lean statement):", "",
                    f"    The conjecture \"{quoted}\" is true; a proof, checked in Lean 4 + Mathlib, "
                    f"is at {url or '<link to the published result>'}.", ""]
        elif r["status"] == "machine-refuted":
            out += ["Counterexample, checked in Lean 4:", "", "```", mf._witness(r)[:1500], "```", ""]
        elif r["status"] == "known":
            nov = r.get("novelty") or {}
            out += [f"Known: {nov.get('reasoning', '')}", ""] + [f"- {h}" for h in nov.get("matching_hits") or []] + [""]
        elif r["status"] == "mistranslated":
            out += [f"The restatement does not match the listed terms: {mf._verdict_line(r['agree']['output'], 300)}", ""]
        elif r["status"] == "inconclusive" and "falsification" not in r:
            out += [f"Agreement check gave no usable verdict: {mf._verdict_line(r['agree']['output'], 300)}", ""]
        elif r["status"] in ("inconclusive", "refuted"):
            out += [f"Search: {mf._verdict_line(r['falsification']['output'], 300)}", ""]
        elif r["status"] in ("provisional", "verified"):
            lean = r.get("lean") or {}
            state = "sorry-free" if lean.get("sorry_free") else "compiles, not sorry-free" if lean.get("compiles") else "not checked"
            out += [f"Referee: {(r.get('referee') or {}).get('verdict')}; independent check: "
                    f"{mf._verdict_line((r.get('independent_check') or {}).get('output', ''), 120)}; Lean: {state}", ""]
        elif r.get("error"):
            out += [f"Error: {r['error']}", ""]
    for line, why in rejected:
        out += [f"- not pursued: {line[:160]} -- {why}"]
    return "\n".join(out) + "\n"


def _pursue(forge, run, c: dict) -> dict:
    """The agreement gate, then mathforge's pipeline."""
    e = c["entry"]
    c = {k: v for k, v in c.items() if k != "entry"}
    try:
        check = run.stage(f"{c['id']}.agree", lambda: forge.agree(e, c))
    except Exception as exc:
        return {**c, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
    verdict = terms_agree(check)
    if verdict is False:
        return {**c, "status": "mistranslated", "agree": check}
    if verdict is None:
        return {**c, "status": "inconclusive", "agree": check}
    return {**mf.run_one(forge, c), "agree": check}


def research(forge_for, e: dict, workers: int = 1, publish: bool = False) -> dict:
    """Every open line of one sequence, end to end. Returns the index summary."""
    a = anum(e)
    run = mf.Run(OUTPUT_ROOT / a)
    run.data.update(oeis=a, seed=f"OEIS {a}: {e.get('name', '')}")
    forge = forge_for(run)
    started = time.time()
    mf.rule(f"{a} {e.get('name', '')[:60]}")
    lines = conjecture_lines(e)[:MAX_PER_ENTRY]

    def triage(line):
        try:
            return run.stage(f"triage-{_key(line)}", lambda: forge.triage(e, line))
        except Exception as exc:  # not cached: a rerun asks again
            return {"usable": False, "reason": f"triage error: {type(exc).__name__}: {exc}"}

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(lines) or 1))) as pool:
        verdicts = list(pool.map(triage, lines))
    refs = mf._clean("\n".join((e.get("reference") or []) + (e.get("link") or [])), 3000)
    conjectures, rejected = [], []
    for line, t in zip(lines, verdicts):
        if t.get("usable") and t.get("statement"):
            conjectures.append({**t, "id": f"c{_key(line)}", "oeis": a, "line": line, "oeis_refs": refs, "entry": e})
        else:
            rejected.append((line, t.get("reason", "")))

    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(conjectures) or 1))) as pool:
        results = list(pool.map(lambda c: _pursue(forge, run, c), conjectures))
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
    if os.environ["AI_WRITING_MODEL"] == os.environ["AI_REVIEW_MODEL"]:
        mf.log("WARNING     work and review are the same model: every independent re-check shares its "
               "blind spots. Pass --review-model with a different model.")
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
