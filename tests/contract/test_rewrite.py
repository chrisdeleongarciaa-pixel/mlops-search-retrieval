import json
import unittest
from search_retrieval.query.rewrite import validate_rewrite


class RewriteContract(unittest.TestCase):
    def test_preserves_explicit_literals(self):
        original = 'papers on "BERT" without supervision'
        payload = json.dumps({"action": "rewrite", "rewritten_text": 'research on "BERT" without supervision'})
        self.assertIn('"BERT"', validate_rewrite(original, payload, required_literals=('"BERT"', 'without supervision')))
        with self.assertRaises(ValueError):
            validate_rewrite(original, payload.replace('without supervision', 'with supervision'), required_literals=('without supervision',))

    def test_invalid_incomplete_duplicate_fields_and_keep_change(self):
        with self.assertRaises(ValueError):
            validate_rewrite("original", '{"action":"keep","rewritten_text":"original"}', finish_reason="length")
        for payload in ('{"action":"rewrite"}', '{"action":',
                        '{"action":"keep","action":"rewrite","rewritten_text":"x"}',
                        '{"action":"keep","rewritten_text":"changed"}',
                        '{"action":"rewrite","rewritten_text":"x","filters":{"tenant":"other"}}'):
            with self.assertRaises(ValueError):
                validate_rewrite("original", payload)
