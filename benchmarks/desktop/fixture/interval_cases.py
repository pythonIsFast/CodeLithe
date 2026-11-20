import unittest

from intervals import merge_intervals


class IntervalTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(merge_intervals([]), [])

    def test_unsorted_overlap_and_containment(self):
        intervals = [(8, 10), (2, 4), (1, 6), (9, 12)]
        self.assertEqual(merge_intervals(intervals), [(1, 6), (8, 12)])
        self.assertEqual(intervals, [(8, 10), (2, 4), (1, 6), (9, 12)])

    def test_shared_endpoint_passes_through_a_chain(self):
        self.assertEqual(merge_intervals([(5, 8), (1, 3), (3, 5)]), [(1, 8)])

    def test_distinct_adjacent_endpoints_and_generator(self):
        intervals = (pair for pair in [(-3, -1), (0, 0), (1, 2)])
        self.assertEqual(merge_intervals(intervals), [(-3, -1), (0, 0), (1, 2)])

    def test_reversed_interval(self):
        with self.assertRaises(ValueError):
            merge_intervals([(1, 3), (7, 4)])
