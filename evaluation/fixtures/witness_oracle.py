import json
import math
import sys
from pathlib import Path

fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
docs = fixture["documents"]
query = fixture["queries"]["retencion-es"]


def check(condition, message):
    if not condition:
        raise ValueError(message)


def exact_cosine_top_k(documents, vector, allowed_ids, k):
    query_norm = math.hypot(*vector)
    check(query_norm > 0 and math.isfinite(query_norm), "invalid query norm")
    scored = []
    for doc_id in allowed_ids:
        other = documents[doc_id]["vector"]
        norm = math.hypot(*other)
        check(len(other) == len(vector), "dimension mismatch")
        check(norm > 0 and math.isfinite(norm), "invalid document norm")
        score = math.fsum(a * b for a, b in zip(vector, other)) / (query_norm * norm)
        scored.append((-score, doc_id))
    return [doc_id for _, doc_id in sorted(scored)[:k]]


def validated_ids(ranking, allowed_ids, k):
    found = set(ranking)
    check(len(ranking) <= k, "ranking exceeds k")
    check(len(found) == len(ranking), "duplicate result")
    check(found <= allowed_ids, "ineligible result")
    return found


eligible = {doc_id for doc_id, doc in docs.items() if doc["tenant"] == query["tenant"]}
exact = exact_cosine_top_k(docs, query["vector"], eligible, k=2)
check(exact == fixture["exact_top2"], "wrong exact order")
approx = validated_ids(fixture["approximate_run_top2"], eligible, k=2)
relevant = {doc_id for doc_id, label in query["qrels"].items() if label > 0}
check(relevant <= eligible, "fixture qrels include ineligible positives")
observed = {
    "ann_recall_at_2": len(approx & set(exact)) / len(exact),
    "approximate_relevance_recall_at_2": len(approx & relevant) / len(relevant),
}
check(observed == fixture["expected"], "metric mismatch")
for mutated in (["other-tenant", "es-protocolo"], ["es-protocolo", "es-protocolo"]):
    try:
        validated_ids(mutated, eligible, k=2)
    except ValueError:
        continue
    raise AssertionError(f"undetected invalid ranking: {mutated}")
print(json.dumps(observed, sort_keys=True))
