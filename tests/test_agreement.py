"""source: agreement math for labels, chance-corrected agreement, and the bootstrap interval.

A test fails when the function it covers does nothing: each one asserts a
concrete return value, so None or an empty result is red.
"""

import os
import random
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agreement import bootstrap, confusion, interval, kappa, matches, norm


def draw_oracle(pairs, fn, n_resamples, seed):
    """Same draws the tool must make: random.Random(seed).randrange, with replacement."""
    rng = random.Random(seed)
    values = []
    size = len(pairs)
    for _resample in range(n_resamples):
        drawn = []
        for _draw in range(size):
            drawn.append(pairs[rng.randrange(size)])
        values.append(fn(drawn))
    return values


class NormTest(unittest.TestCase):
    def test_trim_case_and_inner_space(self):
        """source: norm collapses whitespace and casefolds, so Urgent and urgent are one label."""
        self.assertEqual(norm("  Urgent  "), "urgent")
        self.assertEqual(norm("URGENT"), "urgent")
        self.assertEqual(norm("urgent"), "urgent")
        self.assertEqual(norm("a   b\tc\nd"), "a b c d")
        self.assertEqual(norm(""), "")
        self.assertEqual(norm("   "), "")


class MatchesTest(unittest.TestCase):
    def test_counts_equal_labels_only(self):
        """source: matches counts pairs whose two labels are equal, including a blank label."""
        pairs = [("urgent", "urgent"), ("urgent", "spam"), ("", ""), ("normal", "normal")]
        self.assertEqual(matches(pairs), 3)
        self.assertEqual(matches([]), 0)


class KappaTest(unittest.TestCase):
    def test_two_label_table_is_0_4(self):
        """source: counts 20, 5, 10, 15.

        Rows are the first label, columns the second:
            20  5
            10  15
        n = 50, matches = 35, observed = 0.7.
        Shares are 0.5 and 0.5 against 0.6 and 0.4, so expected = 0.5.
        kappa = (0.7 - 0.5) / (1 - 0.5) = 0.4.
        """
        pairs = [("a", "a")] * 20 + [("a", "b")] * 5 + [("b", "a")] * 10 + [("b", "b")] * 15
        self.assertEqual(round(kappa(pairs), 10), 0.4)

    def test_perfect_agreement_over_two_labels_is_1(self):
        """source: perfect agreement over two labels gives 1.0."""
        pairs = [("a", "a")] * 10 + [("b", "b")] * 10
        self.assertEqual(kappa(pairs), 1.0)

    def test_single_label_is_not_defined(self):
        """source: a single label gives None, because expected agreement is already 1."""
        self.assertIsNone(kappa([("a", "a")] * 5))
        self.assertIsNone(kappa([]))

    def test_three_label_table_worked_by_hand(self):
        """source: three-label table worked by hand.

        Rows are the first label, columns the second. Counts:
            a  b  c
        a   8  1  1   (10)
        b   2  6  2   (10)
        c   0  1  9   (10)
        n = 30, matches = 23, observed = 23/30.
        Second-label shares are 10/30, 8/30, 12/30.
        expected = (10*10 + 10*8 + 10*12) / 900 = 300/900 = 1/3.
        kappa = (23/30 - 1/3) / (1 - 1/3) = (13/30) / (20/30) = 13/20 = 0.65.
        """
        pairs = (
            [("a", "a")] * 8
            + [("a", "b")] * 1
            + [("a", "c")] * 1
            + [("b", "a")] * 2
            + [("b", "b")] * 6
            + [("b", "c")] * 2
            + [("c", "b")] * 1
            + [("c", "c")] * 9
        )
        self.assertEqual(len(pairs), 30)
        self.assertEqual(round(kappa(pairs), 10), 0.65)


class ConfusionTest(unittest.TestCase):
    def test_counts_each_pair(self):
        """source: confusion maps each (mine, grader) pair to how many times it appears."""
        pairs = [("a", "a"), ("a", "b"), ("a", "a")]
        self.assertEqual(confusion(pairs), {("a", "a"): 2, ("a", "b"): 1})
        self.assertEqual(confusion([]), {})


class BootstrapTest(unittest.TestCase):
    def test_same_seed_same_list_and_draw_size(self):
        """source: bootstrap returns n_resamples values, each from len(pairs) draws, fixed by the seed."""
        pairs = [("a", "a"), ("a", "b"), ("b", "b"), ("b", "a")]

        def fn(drawn):
            return (len(drawn), tuple(drawn))

        first = bootstrap(pairs, fn, n_resamples=25, seed=3)
        second = bootstrap(pairs, fn, n_resamples=25, seed=3)
        third = bootstrap(pairs, fn, n_resamples=25, seed=4)
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(len(first), 25)
        self.assertEqual(len(third), 25)
        for length, drawn in first:
            self.assertEqual(length, len(pairs))
            self.assertEqual(len(drawn), len(pairs))
            for item in drawn:
                self.assertIn(item, pairs)
        self.assertEqual(first, draw_oracle(pairs, fn, 25, 3))
        self.assertEqual(third, draw_oracle(pairs, fn, 25, 4))

    def test_empty_pool_still_returns_one_value_per_resample(self):
        """source: drawing zero pairs does not call randrange; each resample is the empty draw."""
        got = bootstrap([], lambda drawn: len(drawn), n_resamples=4, seed=0)
        self.assertEqual(got, [0, 0, 0, 0])


class IntervalTest(unittest.TestCase):
    def test_indexes_on_100_and_2000(self):
        """source: interval indexes on a known list of 100 and of 2000 values.

        For m = 100 and alpha = 0.05, lo = int(100 * 0.05 / 2) = 2 and
        hi = int(100 * 0.975) - 1 = 96.
        For m = 2000, lo = int(2000 * 0.025) = 50 and
        hi = int(2000 * 0.975) - 1 = 1949.
        """
        hundred = list(range(100))
        self.assertEqual(interval(hundred, 0.05), (2, 96))
        self.assertEqual(interval(list(range(99, -1, -1)), 0.05), (2, 96))
        self.assertEqual(interval(list(range(2000)), 0.05), (50, 1949))

    def test_drops_none_and_empty_is_undefined(self):
        """source: None is dropped before the indexes; nothing left is (None, None)."""
        mixed = [None, 5, None, 1, 9]
        # kept [1, 5, 9], lo = int(3 * 0.025) = 0, hi = int(3 * 0.975) - 1 = 1
        self.assertEqual(interval(mixed, 0.05), (1, 5))
        self.assertEqual(interval([], 0.05), (None, None))
        self.assertEqual(interval([None, None], 0.05), (None, None))
        self.assertEqual(interval([7], 0.05), (7, 7))


if __name__ == "__main__":
    unittest.main()
