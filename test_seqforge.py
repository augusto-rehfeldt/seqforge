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

    def test_novelty_asks_whether_a_proof_is_published(self):
        ai = StubAI(['{"verdict": "APPARENTLY_NEW"}'])
        forge = sf.SeqForge(ai, sf.mf.Run(self.tmp / "n"), None, search=False)
        c = {"id": "c1", "oeis": "A069429", "statement": "a(n) = 6a(n-1) - 4a(n-2)"}
        self.assertEqual(forge.novelty(c)["verdict"], "APPARENTLY_NEW")
        self.assertIn("published PROOF", ai.prompts[0])

    def test_research_runs_usable_conjectures_and_drafts_an_oeis_comment(self):
        triaged = {"usable": True, "title": "t", "statement": "for all n >= 1, a(n) = ...",
                   "notation": "a(n) as in the name", "search_space": "n <= 40"}
        forge = mock.Mock()
        forge.triage.side_effect = [triaged, {"usable": False, "reason": "vague"}]
        verified = {"status": "machine-verified", "lean": {"code": "theorem main_theorem : True := trivial"}}
        with mock.patch.object(sf.mf, "run_one", side_effect=lambda f, c: {**c, **verified}) as run_one:
            summary = sf.research(lambda run: forge, ENTRY, workers=2)
        self.assertEqual(run_one.call_count, 1)
        self.assertEqual(summary["tally"], {"machine-verified": 1, "unusable": 1})
        report = (self.tmp / "A069429" / "report.md").read_text(encoding="utf-8")
        self.assertIn("Empirical G.f.", report)
        self.assertIn("Lean 4", report)  # the draft comment for the OEIS editors
        self.assertEqual(sf.done_ids(), {"A069429"})
        # a rerun of the same entry replaces its index entry instead of duplicating it
        forge.triage.side_effect = None
        with mock.patch.object(sf.mf, "run_one", side_effect=lambda f, c: {**c, **verified}):
            sf.research(lambda run: forge, ENTRY, workers=1)
        self.assertEqual(len(json.loads((self.tmp / "index.json").read_text(encoding="utf-8"))), 1)


if __name__ == "__main__":
    unittest.main()
