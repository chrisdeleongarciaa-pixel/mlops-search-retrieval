from time import monotonic
import unittest

from search_retrieval.query.delivery import Delivery, DeliveryBusy, DeliveryRejected
from search_retrieval.query.eligible import Eligibility, Hit, SearchResult, SearchUnavailable


class Tokenizer:
    def count(self, messages):
        return len(messages[1]["content"])


class Backend:
    def __init__(self, change=lambda: None, citation="fragment"):
        self.change = change
        self.citation = citation
        self.calls = 0
        self.closed = False

    def stream(self, messages, max_tokens, deadline):
        self.calls += 1
        try:
            yield '{"text":"siete días",'
            self.change()
            yield f'"citations":["{self.citation}"]}}'
        finally:
            self.closed = True


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.allowed = Eligibility("lab", "generation", frozenset({"projection"}))
        self.revoked = Eligibility("lab", "generation", frozenset())
        self.current = self.allowed
        self.result = SearchResult(self.allowed, (Hit("projection", "fragment", "siete días", 1),))
        self.deadline = monotonic() + 30

    def revoke(self):
        self.current = self.revoked

    def test_dl01_revocation_before_context_prevents_generation(self):
        self.revoke()
        backend = Backend()
        with self.assertRaises(DeliveryRejected):
            Delivery(Tokenizer(), backend).private_reply(
                "¿Cuánto?", self.result, lambda: self.current, deadline=self.deadline
            )
        self.assertEqual(backend.calls, 0)

    def test_dl02_revocation_during_generation_discards_complete_private_answer(self):
        backend = Backend(self.revoke)
        with self.assertRaises(DeliveryRejected):
            Delivery(Tokenizer(), backend).private_reply(
                "¿Cuánto?", self.result, lambda: self.current, deadline=self.deadline
            )
        self.assertTrue(backend.closed)

    def test_dl03_revocation_authority_failure_discards_private_answer(self):
        calls = 0

        def authority():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise SearchUnavailable()
            return self.allowed

        with self.assertRaises(SearchUnavailable):
            Delivery(Tokenizer(), Backend()).private_reply(
                "¿Cuánto?", self.result, authority, deadline=self.deadline
            )

    def test_dl04_citation_to_truncated_evidence_is_rejected(self):
        evidence = Hit("projection", "dropped", "a" * 400, 1)
        result = SearchResult(self.allowed, (*self.result.hits, evidence))
        delivery = Delivery(Tokenizer(), Backend(citation="dropped"), context_limit=175, output_reserve=20)
        with self.assertRaisesRegex(DeliveryRejected, "Citation absent"):
            delivery.private_reply("¿Cuánto?", result, lambda: self.current, deadline=self.deadline)

    def test_fragment_scope_is_enforced_at_context_and_final_delivery(self):
        excluded = Eligibility("lab", "generation", frozenset({"projection"}), frozenset())
        backend = Backend()
        delivery = Delivery(Tokenizer(), backend)
        with self.assertRaisesRegex(DeliveryRejected, "No eligible evidence"):
            delivery.private_reply("¿Cuánto?", self.result, lambda: excluded, deadline=self.deadline)
        self.assertEqual(backend.calls, 0)
        reads = iter((self.allowed, excluded))
        with self.assertRaisesRegex(DeliveryRejected, "revoked before delivery"):
            delivery.private_reply("¿Cuánto?", self.result, lambda: next(reads), deadline=self.deadline)
        self.assertEqual(backend.calls, 1)

    def test_dl05_disconnect_closes_generator_and_records_terminal(self):
        backend = Backend()
        status = []
        stream = Delivery(Tokenizer(), backend).public_stream(
            "¿Cuánto?", self.result, lambda: self.current, deadline=self.deadline, terminal=status.append
        )
        self.assertEqual(next(stream).kind, "provisional")
        stream.close()
        self.assertTrue(backend.closed)
        self.assertEqual(status, ["disconnected"])

    def test_dl06_public_terminal_contains_verified_citation(self):
        statuses = []
        events = list(Delivery(Tokenizer(), Backend()).public_stream(
            "¿Cuánto?", self.result, lambda: self.current,
            deadline=self.deadline, terminal=statuses.append
        ))
        self.assertEqual([event.kind for event in events], ["provisional", "provisional", "complete"])
        self.assertEqual(events[-1].payload.citations, ("fragment",))
        self.assertEqual(statuses, ["complete"])

    def test_dl07_saturation_rejects_until_disconnect_releases_slot(self):
        delivery = Delivery(Tokenizer(), Backend(), max_inflight=1)
        stream = delivery.public_stream(
            "¿Cuánto?", self.result, lambda: self.current,
            deadline=self.deadline, terminal=lambda status: None,
        )
        try:
            self.assertEqual(next(stream).kind, "provisional")
            with self.assertRaises(DeliveryBusy):
                delivery.private_reply("¿Cuánto?", self.result, lambda: self.current, deadline=self.deadline)
        finally:
            stream.close()
        answer = delivery.private_reply("¿Cuánto?", self.result, lambda: self.current, deadline=self.deadline)
        self.assertEqual(answer.citations, ("fragment",))
