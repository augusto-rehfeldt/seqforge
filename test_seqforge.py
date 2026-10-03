"""Offline checks for seqforge: no network, no model calls, temporary output."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import seqforge as sf

ENTRY = {
    "number": 69429,
    "name": "Half the number of 3 X n binary arrays with no path of adjacent 1's from top row to bottom row.",
    "data": "1,5,22,92,380",
    "formula": [
        "Empirical G.f.: x*(3-2*x)/(1-6*x+4*x^2). - _Colin Barker_, Feb 22 2012",
        "Conjecture: a(n) ~ 3^n/sqrt(n).",
        "Conjecture: a(n) = 2*a(n-1) + 1, proved by _Someone_, 2015.",
        "a(n) = 6*a(n-1) - 4*a(n-2).",
    ],
    "comment": ["Empirical: a(n) = a(n-1) + 4^(n-1) for n >= 2."],
}


class StubAI:
    def __init__(self, replies):
        self.replies, self.prompts = list(replies), []

    def generate_content(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.replies.pop(0)


class SeqforgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(sf, "OUTPUT_ROOT", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_conjecture_lines_keep_open_exact_claims_only(self):
        lines = sf.conjecture_lines(ENTRY)
        self.assertEqual(lines, [ENTRY["formula"][0], ENTRY["comment"][0]])

    def test_filter_catches_lowercase_gf_and_settled_or_asymptotic_wording(self):
        e = {"number": 2, "name": "x", "formula": [
            "Empirical g.f.: x/(1-2*x). - _Colin Barker_, May 1 2013",
            "Conjecture: a(n) = 2^n. The conjecture is true.",
            "Conjecture: a(n) is asymptotic to 3^n = b(n)."]}
        self.assertEqual(sf.conjecture_lines(e), [e["formula"][0]])
        settled = {**e, "comment": ["The conjectures above are true (_A. Howroyd_, 2020)."]}
        self.assertEqual(sf.conjecture_lines(settled), [])

    def test_search_parses_oeis_json_and_tolerates_no_results(self):
        with mock.patch.object(sf.mf, "_http_get", return_value=json.dumps([ENTRY])) as get:
            self.assertEqual(sf.search("formula:conjecture", 10), [ENTRY])
        self.assertIn("start=10", get.call_args[0][0])
        self.assertIn("fmt=json", get.call_args[0][0])
        with mock.patch.object(sf.mf, "_http_get", return_value="null"):
            self.assertEqual(sf.search("nothing"), [])

    def test_targets_skip_done_and_conjecture_free_entries(self):
        bare = {"number": 1, "name": "x", "formula": ["a(n) = n."]}
        done = {**ENTRY, "number": 5}
        pages = {0: [bare, done, ENTRY], 10: []}
        with mock.patch.object(sf, "search", side_effect=lambda q, start=0: pages.get(start, [])):
            got = sf.targets(["formula:conjecture"], {"A000005"}, limit=5)
        self.assertEqual([sf.anum(e) for e in got], ["A069429"])

    def test_triage_asks_the_review_model_with_the_whole_entry(self):
        ai = StubAI(['{"usable": false, "reason": "asymptotic"}'])
        forge = sf.SeqForge(ai, sf.mf.Run(self.tmp / "t"), None, search=False)
        self.assertFalse(forge.triage(ENTRY, ENTRY["formula"][0])["usable"])
        self.assertIn("A069429", ai.prompts[0])
        self.assertIn("proved by _Someone_", ai.prompts[0])  # settled lines reach the judge

    def test_research_runs_usable_conjectures_and_drafts_an_oeis_comment(self):
        triaged = {"usable": True, "title": "t", "statement": "for all n >= 1, a(n) = ...",
                   "notation": "a(n) as in the name", "search_space": "n <= 40"}
        forge = mock.Mock()
        forge.triage.side_effect = [triaged, {"usable": False, "reason": "vague"}]
        forge.agree.return_value = {"exit_code": 0, "output": "TERMS AGREE: formulas=12 definition=8", "code": ""}
        verified = {"status": "machine-verified", "lean": {"code": "theorem main_theorem : True := trivial"}}
        with mock.patch.object(sf.mf, "run_one", side_effect=lambda f, c: {**c, **verified}) as run_one:
            summary = sf.research(lambda run: forge, ENTRY, workers=2)
        self.assertEqual(run_one.call_count, 1)
        self.assertEqual(summary["tally"], {"machine-verified": 1, "unusable": 1})
        report = (self.tmp / "A069429" / "report.md").read_text(encoding="utf-8")
        self.assertIn("Empirical G.f.", report)
        self.assertIn("Lean 4", report)  # the draft comment for the OEIS editors
        self.assertNotIn("Colin Barker_, Feb 22 2012\" is true", report)  # quoted without its signature
        self.assertEqual(sf.done_ids(), {"A069429"})
        # a rerun of the same entry replaces its index entry instead of duplicating it
        forge.triage.side_effect = None
        with mock.patch.object(sf.mf, "run_one", side_effect=lambda f, c: {**c, **verified}):
            sf.research(lambda run: forge, ENTRY, workers=1)
        self.assertEqual(len(json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))), 1)



class SeqforgeGateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        patcher = mock.patch.object(sf, "OUTPUT_ROOT", self.tmp)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.usable = {"usable": True, "statement": "s", "notation": "", "search_space": "n <= 40"}

    def test_a_restatement_that_disagrees_with_the_listed_terms_is_not_pursued(self):
        forge = mock.Mock()
        forge.triage.return_value = self.usable
        forge.agree.return_value = {"exit_code": 0, "output": "TERMS DIFFER at n=3: 22 vs 21", "code": ""}
        with mock.patch.object(sf.mf, "run_one") as run_one:
            summary = sf.research(lambda run: forge, {**ENTRY, "comment": []})
        run_one.assert_not_called()
        self.assertEqual(summary["tally"], {"mistranslated": 1})

    def test_stages_are_keyed_by_line_content_not_position(self):
        forge = mock.Mock()
        forge.triage.side_effect = lambda e, line: {**self.usable, "statement": line}
        forge.agree.return_value = {"exit_code": 0, "output": "TERMS AGREE: formulas=12 definition=8", "code": ""}
        with mock.patch.object(sf.mf, "run_one", side_effect=lambda f, c: {**c, "status": "verified"}):
            sf.research(lambda run: forge, ENTRY)
            moved = {**ENTRY, "formula": ["Conjecture: a(n) = 3*a(n-1) for n > 7."] + ENTRY["formula"]}
            results = sf.research(lambda run: forge, moved) and json.loads(
                (self.tmp / "A069429" / "state.json").read_text(encoding="utf-8"))["results"]
        for r in results:
            self.assertEqual(r["statement"], r["line"])  # each cached verdict stayed with its own line

    def test_a_failed_triage_is_recorded_not_fatal(self):
        forge = mock.Mock()
        forge.triage.side_effect = ValueError("no JSON")
        summary = sf.research(lambda run: forge, ENTRY)
        self.assertEqual(summary["tally"], {"unusable": 2})
        self.assertIn("no JSON", (self.tmp / "A069429" / "report.md").read_text(encoding="utf-8"))

    def test_published_url_and_known_hits_reach_the_report(self):
        r = {"id": "c1", "line": "Conjecture: a(n) = 1. - _X Y_, Jan 1 2020", "statement": "s",
             "status": "machine-verified", "published": {"url": "https://github.com/u/r/tree/main/x"}}
        k = {"id": "c2", "line": "l", "statement": "s", "status": "known",
             "novelty": {"matching_hits": ["Howroyd, A069361 formula"]}}
        text = sf.report(ENTRY, [r, k], [])
        self.assertIn("is at https://github.com/u/r/tree/main/x", text)
        self.assertIn('"Conjecture: a(n) = 1." is true', text)
        self.assertIn("Howroyd, A069361 formula", text)

    def test_novelty_keeps_queries_clean_and_judges_proofs_with_oeis_context(self):
        ai = StubAI(['{"verdict": "KNOWN", "matching_hits": []}',
                     '{"verdict": "APPARENTLY_NEW", "reasoning": "only conjectured", "matching_hits": []}'])
        forge = sf.SeqForge(ai, sf.mf.Run(self.tmp / "n2"), None, search=False)
        c = {"id": "c1", "oeis": "A069429", "statement": "a(n) = 6a(n-1) - 4a(n-2)",
             "oeis_refs": "Andrew Howroyd, Table of n, a(n)"}
        got = forge.novelty(c)
        self.assertNotIn("published PROOF", ai.prompts[0])
        self.assertIn("published PROOF", ai.prompts[1])
        self.assertIn("Andrew Howroyd", ai.prompts[1])
        self.assertEqual(got["verdict"], "APPARENTLY_NEW")
        self.assertEqual(got["generic_verdict"], "KNOWN")


class SeqforgeAgreementTest(unittest.TestCase):
    def test_agreement_needs_enough_terms_from_the_formulas_and_the_definition(self):
        ok = lambda out: sf.terms_agree({"exit_code": 0, "output": out})
        self.assertTrue(ok("TERMS AGREE: formulas=12 definition=6"))
        self.assertTrue(ok("TERMS AGREE: formulas = 12, definition = 6"))  # punctuation is no reason to skip
        self.assertIsNone(ok("TERMS AGREE: formulas=0 definition=0"))  # compared nothing
        self.assertIsNone(ok("TERMS AGREE: formulas=12 definition=2"))  # the definition barely ran
        self.assertIsNone(ok("TERMS AGREE"))
        self.assertFalse(ok("TERMS DIFFER: definition n=3 gives 21, listed 22"))
        self.assertIsNone(sf.terms_agree({"exit_code": 1, "output": "Traceback"}))

    def test_ids_are_decimal_and_lines_unique(self):
        self.assertRegex(sf._key("Conjecture: a(n) = n."), r"^[0-9]{7}$")  # mathforge tags need c<digits>
        twice = {"number": 3, "name": "x", "formula": ["Conjecture: a(n) = n."], "comment": ["Conjecture: a(n) = n."]}
        self.assertEqual(sf.conjecture_lines(twice), ["Conjecture: a(n) = n."])

    def test_every_status_is_explained_in_the_report(self):
        rs = [{"id": "c1", "line": "l", "statement": "s", "status": "inconclusive",
               "agree": {"output": "TERMS AGREE: formulas=1 definition=0"}},
              {"id": "c2", "line": "l", "statement": "s", "status": "provisional", "referee": {"verdict": "GAPS"},
               "independent_check": {"output": "ALL CHECKS PASSED"}, "lean": {"compiles": True, "sorry_free": False}}]
        text = sf.report(ENTRY, rs, [])
        self.assertIn("formulas=1", text)
        self.assertIn("GAPS", text)


if __name__ == "__main__":
    unittest.main()
