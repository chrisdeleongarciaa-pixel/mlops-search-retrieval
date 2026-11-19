"""Retrieval service, its HTTP front end, the TEI client, open-loop load and revocation timing."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from time import sleep
import unittest
import urllib.request

import numpy as np

from search_retrieval.adapters.tei import TeiEncoder, TeiSpace
from search_retrieval.adapters.vespa_passages import Passage, VespaPassages
from search_retrieval.contracts.cohorts import cohort_bucket
from search_retrieval.query.eligible import SearchUnavailable
from search_retrieval.serving.http import handler_for
from search_retrieval.serving.service import Fusion, PublicAuthority, RetrievalService
from acl_revocation import FixedEncoder, measure_one
from fake_services import FakeTei, FakeVespa
from load import Client, analyse, run_point


TEMPLATE = "task: search result | query: {text}"


def unit(rows):
    return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


class MemoryAuthority:
    """Private documents granted to principals, in memory."""

    def __init__(self, grants):
        self.grants = dict(grants)
        self.fail = False

    def tokens(self, principal):
        return ["public"] if principal is None else ["public", principal]

    def denied(self, principal, doc_ids):
        if self.fail:
            raise SearchUnavailable("authority down")
        return {doc for doc in doc_ids if doc in self.grants and self.grants[doc] != principal}

    def revoke(self, doc_id, principal):
        self.grants[doc_id] = None


class Fixture(unittest.TestCase):
    def setUp(self):
        self.vespa, self.tei = FakeVespa(), FakeTei(dimensions=8)
        self.addCleanup(self.vespa.close)
        self.addCleanup(self.tei.close)
        self.engine = VespaPassages(self.vespa.url, timeout=5)
        rng = np.random.default_rng(12)
        self.vectors = unit(rng.standard_normal((20, 8)))
        self.ids = [f"p{index:02d}" for index in range(20)]
        self.engine.feed("live", iter(Passage(item, f"topic{index % 4} paper", self.vectors[index].tolist(),
                                              cohort_bucket(item), ("public",) if index else ("alice",))
                                      for index, item in enumerate(self.ids)), threads=2)
        self.encoder = TeiEncoder(self.tei.url, TeiSpace(TEMPLATE, 8), timeout=5)


class TeiTests(Fixture):
    def test_the_client_renders_the_space_prompt_and_validates_rows(self):
        rows = self.encoder.encode_queries(["graph neural networks"])
        self.assertEqual(self.tei.requests[-1]["inputs"], ["task: search result | query: graph neural networks"])
        self.assertEqual(self.tei.requests[-1]["normalize"], True)
        self.assertAlmostEqual(float(np.linalg.norm(rows[0])), 1.0, places=5)
        self.tei.vector = lambda text: [1.0] * 8
        with self.assertRaisesRegex(SearchUnavailable, "non-unit"):
            self.encoder.encode_queries(["x"])
        self.tei.vector = lambda text: [1.0, 0.0]
        with self.assertRaisesRegex(SearchUnavailable, "wrong shape"):
            self.encoder.encode_queries(["x"])
        self.tei.fail = True
        with self.assertRaisesRegex(SearchUnavailable, "503"):
            self.encoder.encode_queries(["x"])
        with self.assertRaises(ValueError):
            TeiSpace.from_body({"normalize": False, "query_template": TEMPLATE, "dim": 8})


class ServiceTests(Fixture):
    def service(self, authority=None, fusion=Fusion()):
        return RetrievalService(self.engine, self.encoder, authority or PublicAuthority(), "live", fusion=fusion,
                                branch_depth=20)

    def test_routes_return_ranked_hits_with_stage_times(self):
        service = self.service()
        lexical = service.search("topic2 paper", route="bm25", k=5)
        self.assertEqual(lexical.hits[0][0][:1], "p")
        self.assertEqual(set(lexical.stages_ms), {"engine_lexical", "revalidation", "service_total"})
        dense = service.search("a query", route="dense", k=3)
        self.assertEqual(set(dense.stages_ms), {"encode", "engine_dense", "revalidation", "service_total"})
        hybrid = service.search("topic1", route="hybrid", k=5)
        self.assertEqual(set(hybrid.stages_ms), {"encode", "engine_lexical", "engine_dense", "fusion", "revalidation",
                                                 "service_total"})
        self.assertLessEqual(len(hybrid.hits), 5)
        convex = self.service(fusion=Fusion("convex", 0.7)).search("topic1", route="hybrid", k=5)
        self.assertLessEqual(len(convex.hits), 5)
        for bad in ({"route": "fuzzy"}, {"k": 0}, {"k": 21}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                service.search("q", **bad)

    def test_parallel_branches_overlap_and_return_the_same_hits(self):
        # The hybrid route's lexical query used to run before encoding and the dense query, so its
        # latency was the sum of both branches; with branches="parallel" it is about the longer one.
        import time
        from unittest import mock

        slow_lexical, slow_encode = self.engine.lexical, self.encoder.encode_queries

        def lexical(*args, **kwargs):
            time.sleep(0.2)
            return slow_lexical(*args, **kwargs)

        def encode(*args, **kwargs):
            time.sleep(0.2)
            return slow_encode(*args, **kwargs)

        results = {}
        with mock.patch.object(self.engine, "lexical", side_effect=lexical), \
                mock.patch.object(self.encoder, "encode_queries", side_effect=encode):
            for mode in ("sequential", "parallel"):
                service = RetrievalService(self.engine, self.encoder, PublicAuthority(), "live", branch_depth=20,
                                           branches=mode)
                answer = service.search("topic1 paper", route="hybrid", k=5)
                results[mode] = answer
        self.assertEqual(results["parallel"].hits, results["sequential"].hits)
        self.assertGreaterEqual(results["sequential"].stages_ms["service_total"], 400)
        self.assertLess(results["parallel"].stages_ms["service_total"], 350)
        self.assertEqual(set(results["parallel"].stages_ms), set(results["sequential"].stages_ms))
        with self.assertRaises(ValueError):
            RetrievalService(self.engine, self.encoder, PublicAuthority(), "live", branches="eventually")

    def test_private_documents_need_a_grant_and_the_authority_has_the_last_word(self):
        authority = MemoryAuthority({"p00": "alice"})
        service = self.service(authority)
        self.tei.vector = lambda text: self.vectors[0].tolist()
        self.assertNotIn("p00", [doc for doc, _ in service.search("q", route="dense", k=5).hits])
        self.assertEqual(service.search("q", route="dense", k=5, principal="alice").hits[0][0], "p00")
        # Revoked at the authority before the engine is updated: delivery already excludes it.
        authority.revoke("p00", "alice")
        answer = service.search("q", route="dense", k=5, principal="alice")
        self.assertNotIn("p00", [doc for doc, _ in answer.hits])
        self.assertEqual(answer.removed_by_authority, 1)
        authority.fail = True
        with self.assertRaises(SearchUnavailable):
            service.search("q", route="dense", k=5, principal="alice")

    def test_http_front_end_maps_outcomes_to_status_codes(self):
        authority = MemoryAuthority({})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.service(authority)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        url = f"http://127.0.0.1:{server.server_address[1]}"

        def post(body):
            request = urllib.request.Request(url + "/search", json.dumps(body).encode(), {"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        status, body = post({"query": "topic3", "route": "bm25", "k": 3})
        self.assertEqual(status, 200)
        self.assertLessEqual(len(body["hits"]), 3)
        self.assertEqual(post({"query": "x", "extra": 1})[0], 400)
        self.assertEqual(post({"query": "x", "k": "3"})[0], 400)
        self.assertEqual(post({"query": "x", "principal": ""})[0], 400)
        authority.fail = True
        self.assertEqual(post({"query": "x", "route": "dense"})[0], 503)
        with urllib.request.urlopen(url + "/health", timeout=5) as response:
            self.assertEqual(json.load(response), {"collection": "live"})


class LoadTests(unittest.TestCase):
    def test_latency_runs_from_the_scheduled_time_so_a_stall_is_not_hidden(self):
        stall = threading.Event()

        class Slow(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                if stall.is_set():
                    sleep(0.3)
                data = json.dumps({"hits": [], "stages_ms": {"engine": 1.0}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        client = Client(f"http://127.0.0.1:{server.server_address[1]}", timeout=5)
        # One worker: while it is blocked, later requests wait in the generator; their wait counts.
        stall.set()
        outcome = run_point(client, ["q"], "bm25", rate=50, count=20, seed=3, k=5, workers=1)
        latencies = [record["latency_ms"] for record in outcome["records"]]
        self.assertGreater(max(latencies), 20 * 300 * 0.5)
        self.assertGreater(max(record["send_lag_ms"] for record in outcome["records"]), 1000)
        result = analyse(outcome["records"], warmup=5)
        self.assertEqual((result["measured"], result["ok"], result["errors"]), (15, 15, 0))
        self.assertEqual(set(result["stages_ms"]), {"engine"})


class RevocationTests(Fixture):
    def test_a_revocation_is_excluded_at_delivery_without_leaks(self):
        authority = MemoryAuthority({"p00": "alice"})
        encoder = FixedEncoder()
        service = RetrievalService(self.engine, encoder, authority, "live", branch_depth=10)
        result = measure_one(service, self.engine, authority, encoder, "live", "p00", "alice", self.vectors[0].tolist())
        self.assertEqual(result["leaks"], 0)
        self.assertIsNotNone(result["delivery_s"])
        self.assertIsNotNone(result["engine_s"])
        self.assertLess(result["delivery_s"], 1.0)

    def test_an_authority_that_does_not_revoke_leaks_and_is_counted(self):
        class Stubborn(MemoryAuthority):
            def revoke(self, doc_id, principal):
                pass

        authority = Stubborn({"p00": "alice"})
        encoder = FixedEncoder()
        service = RetrievalService(self.engine, encoder, authority, "live", branch_depth=10)
        update = self.engine.update_tokens

        def slow_update(*arguments):
            sleep(0.2)
            update(*arguments)

        self.engine.update_tokens = slow_update
        # Nothing excludes the document until the engine update lands 0.2 s later: every response before it leaks.
        result = measure_one(service, self.engine, authority, encoder, "live", "p00", "alice", self.vectors[0].tolist())
        self.assertGreater(result["leaks"], 0)
        self.assertGreaterEqual(result["delivery_s"], 0.2)


    def test_revocations_continue_past_skipped_probes_until_the_target_is_measured(self):
        # 2026-11-18: 317 of 1,000 probes were skipped and ACL-01 stopped at 683 measured revocations.
        from unittest import mock
        import acl_revocation

        class Documents:
            def __init__(self, ids, vectors):
                self.ids, self.vectors, self.n = ids, vectors, len(ids)

            def take(self, positions, dtype):
                return self.vectors[positions].astype(dtype)

        class Ledger(MemoryAuthority):
            def __init__(self):
                super().__init__({})
                self.public_again = []

            def make_private(self, grants):
                self.grants.update(grants)

            def make_public(self, doc_ids):
                self.public_again.extend(doc_ids)

        outcomes = iter([{"skipped": "x"}] * 4 + [{"leaks": 0, "delivery_s": 0.01, "engine_s": 0.02}] * 20)
        authority = Ledger()
        with mock.patch.object(acl_revocation, "measure_one", side_effect=lambda *a, **k: dict(next(outcomes))):
            results = acl_revocation.run_revocations(None, self.engine, authority, FixedEncoder(),
                                                     Documents(self.ids, self.vectors), "live", target=6,
                                                     principals=3, settle_s=0)
        self.assertEqual(sum("skipped" not in item for item in results), 6)
        self.assertEqual(len(results), 10)
        self.assertEqual(sorted(authority.public_again), sorted(self.ids[:12]))

    def test_the_probe_explores_widely(self):
        import acl_revocation
        self.assertGreaterEqual(acl_revocation.PROBE_EXPLORE, 100)


class TeiParityTests(unittest.TestCase):
    def test_compare_applies_the_conformance_tolerance_per_query(self):
        from tei_parity import compare

        canonical = unit(np.random.default_rng(1).standard_normal((4, 8)))
        self.assertTrue(compare(canonical.copy(), canonical)["passed"])
        shifted = canonical.copy()
        shifted[2, 0] += 2e-5
        result = compare(shifted, canonical)
        self.assertFalse(result["passed"])
        self.assertEqual(result["queries_over_error_limit"], 1)
        # The shift is applied to a float32 value, so it is 2e-5 only to float32 resolution.
        self.assertAlmostEqual(result["max_absolute_error"], 2e-5, delta=1e-7)

    def test_ranking_impact_reports_moved_documents_and_ndcg(self):
        from tei_parity import ndcg_at, ranking_impact

        rng = np.random.default_rng(3)
        documents = unit(rng.standard_normal((50, 8)))
        doc_ids = [f"d{index}" for index in range(50)]
        canonical = documents[:4] + 0.0  # each query is its own document: rank 1
        judgments = {f"q{index}": {f"d{index}": 1} for index in range(4)}
        same = ranking_impact(canonical.copy(), canonical, documents, doc_ids, list(judgments), judgments, k=10)
        self.assertEqual((same["identical_top_k"], same["mean_overlap_at_k"], same["mean_ndcg_delta"]), (1.0, 1.0, 0.0))
        moved = canonical.copy()
        moved[0] = documents[7]  # query 0 now retrieves another document first
        result = ranking_impact(moved, canonical, documents, doc_ids, list(judgments), judgments, k=10)
        self.assertEqual(result["identical_top_k"], 0.75)
        self.assertLess(result["mean_ndcg_delta"], 0)
        self.assertEqual(result["judged_queries"], 4)
        self.assertAlmostEqual(ndcg_at(["a", "b"], {"b"}, 10), 1 / np.log2(3))
