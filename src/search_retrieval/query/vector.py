"""Dense and independent-branch RRF retrieval in the mutable witness profile."""

from time import monotonic

from search_retrieval.query.eligible import EligibleSearch, Hit, SearchUnavailable, timed
from search_retrieval.query.ranking import Ranked, fuse


class VectorSearch(EligibleSearch):
    def __init__(self, authority, index, encoder, generation, *, explore=0):
        super().__init__(authority, index)
        self.encoder = encoder
        self.generation = generation
        self.explore = explore

    def _check_snapshot(self, eligibility):
        if eligibility.generation != self.generation:
            raise SearchUnavailable("Incompatible vector generation")

    def search(self, tenant, query, *, mode="dense", approximate=True, k=10, timeout=5, attempts=None):
        if mode not in {"dense", "hybrid"}:
            raise ValueError("Expected dense or hybrid retrieval")

        def rank(snapshot, text, depth, remaining, sample):
            deadline = monotonic() + remaining

            def budget():
                left = deadline - monotonic()
                if left <= 0:
                    raise SearchUnavailable("Vector search deadline expired")
                return left

            space = self.encoder.space.id
            eligible_count = timed(sample, "vector_coverage_ms", self.index.verify_vector_coverage, snapshot, space, budget())
            vector, = timed(sample, "encode_query_ms", self.encoder.encode, [text], role="query")
            branch_depth = 100 if mode == "hybrid" else depth
            dense = timed(
                sample, "vespa_dense_ms", self.index.dense, snapshot, vector, space, budget(),
                approximate=approximate, explore=self.explore, k=branch_depth,
            )
            if (len(dense) != min(branch_depth, eligible_count)
                    or any(not snapshot.permits(hit) for hit in dense)):
                raise SearchUnavailable("Incomplete or ineligible dense branch")
            if mode == "dense":
                return dense
            lexical = timed(sample, "vespa_lexical_ms", self.index.lexical, snapshot, text, 100, budget())
            if any(not snapshot.permits(hit) for hit in lexical):
                raise SearchUnavailable("Ineligible lexical branch")
            by_id = {hit.fragment_id: hit for hit in (*lexical, *dense)}
            run = fuse([Ranked(hit.fragment_id, hit.score) for hit in lexical],
                       [Ranked(hit.fragment_id, hit.score) for hit in dense])
            return tuple(Hit(by_id[item.id].projection_id, item.id, by_id[item.id].text, item.score)
                         for item in run.fused[:depth])

        return self._search(tenant, query, k, timeout, attempts, rank)
