"""Quality from an oracle: positions become document IDs, padding is dropped, shapes must agree."""

import unittest

import numpy as np

from oracle_quality import rankings_from_oracle


class OracleQualityTests(unittest.TestCase):
    def test_positions_become_ids_and_padding_is_dropped(self):
        rows = np.array([[7, 3, -1], [0, 1, 2]])
        scores = np.array([[0.9, 0.5, -np.inf], [0.8, 0.7, 0.6]])
        rankings = rankings_from_oracle(rows, scores, ["q1", "q2"])
        self.assertEqual(rankings["q1"], [("7", 0.9), ("3", 0.5)])
        self.assertEqual([doc for doc, _ in rankings["q2"]], ["0", "1", "2"])
        with self.assertRaises(ValueError):
            rankings_from_oracle(rows, scores[:1], ["q1", "q2"])


if __name__ == "__main__":
    unittest.main()
