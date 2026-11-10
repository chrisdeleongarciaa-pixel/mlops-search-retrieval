"""Freeze a stratified LitSearch-300 title/abstract profile before ranking."""

import argparse
from collections import defaultdict
from hashlib import file_digest, sha256
import json
from pathlib import Path
import os
import shutil
from tempfile import TemporaryDirectory


REVISION = "9573fb284a1026c998df47024b888a163f0f0e25"
SEED = 20261108


def digest(path):
    with path.open("rb") as stream:
        return file_digest(stream, "sha256").hexdigest()


def select_queries(queries, size=300, seed=SEED):
    if len({q["id"] for q in queries}) != len(queries) or not 0 < size <= len(queries):
        raise ValueError("Invalid query population")
    groups = defaultdict(list)
    for query in queries:
        groups[(query["query_set"], query["specificity"])].append(query)
    quotas = {key: size * len(rows) // len(queries) for key, rows in groups.items()}
    remainder = sorted(groups, key=lambda key: (-(size * len(groups[key]) % len(queries)), key))
    for key in remainder[:size - sum(quotas.values())]:
        quotas[key] += 1
    selected = []
    for key, rows in sorted(groups.items()):
        ordered = sorted(rows, key=lambda q: (sha256(f'{seed}:{q["id"]}'.encode()).hexdigest(), q["id"]))
        selected.extend(ordered[:quotas[key]])
    return sorted(selected, key=lambda q: q["id"])


def development_queries(queries, selected):
    """Hold related gold-document and exact-text components out of development."""
    held = {q["id"] for q in selected}
    while True:
        gold = {doc for q in queries if q["id"] in held for doc in q["corpusids"]}
        texts = {" ".join(q["query"].casefold().split()) for q in queries if q["id"] in held}
        expanded = held | {q["id"] for q in queries if gold.intersection(q["corpusids"])
                          or " ".join(q["query"].casefold().split()) in texts}
        if expanded == held:
            break
        held = expanded
    return [q for q in queries if q["id"] not in held]


def recall_scores(queries, rankings):
    if set(rankings) != {q["id"] for q in queries}:
        raise ValueError("Ranking query coverage mismatch")
    rows = {}
    for query in queries:
        docs = rankings[query["id"]]
        relevant = set(query["corpusids"])
        if not relevant or len(set(docs)) != len(docs):
            raise ValueError("Missing judgments or duplicate candidates")
        rows[query["id"]] = {f"Recall@{k}": len(set(docs[:k]) & relevant) / len(relevant) for k in (5, 20)}
    return rows


def prepare(source: Path, output: Path):
    import pyarrow.parquet as parquet

    manifest = json.loads((source / "source-manifest.json").read_text())
    pinned = json.loads((Path(__file__).parents[1] / "config/litsearch-source.json").read_text())
    if manifest != pinned:
        raise ValueError("Source manifest differs from the pinned dataset artifacts")
    if manifest["revision"] != REVISION or manifest["dataset"] != "princeton-nlp/LitSearch":
        raise ValueError("Unexpected LitSearch revision")
    expected = {f"corpus_clean/full-{i:05d}-of-00006.parquet" for i in range(6)}
    expected.add("query/full-00000-of-00001.parquet")
    if {row["path"] for row in manifest["files"]} != expected or len(manifest["files"]) != 7:
        raise ValueError("Unexpected bundle names or coverage")
    for row in manifest["files"]:
        path = source / row["path"]
        if path.stat().st_size != row["bytes"] or digest(path) != row["sha256"]:
            raise ValueError(f'Invalid input {row["path"]}')
    raw_queries = parquet.read_table(source / "query/full-00000-of-00001.parquet").to_pylist()
    queries = [{**q, "corpusids": [str(doc) for doc in q["corpusids"]],
                "id": f"litsearch-{i:04d}", "source_row": i} for i, q in enumerate(raw_queries)]
    if len(queries) != 597:
        raise ValueError("Unexpected query count")
    selected = select_queries(queries)
    development = development_queries(queries, selected)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    with TemporaryDirectory(prefix=".litsearch-stage-", dir=output.parent) as temp:
        stage = Path(temp)
        seen = set()
        with (stage / "corpus.jsonl").open("w", encoding="utf-8", newline="\n") as target:
            for name in sorted(expected - {"query/full-00000-of-00001.parquet"}):
                for row in parquet.read_table(source / name, columns=["corpusid", "title", "abstract"]).to_pylist():
                    doc = str(row["corpusid"])
                    if doc in seen:
                        raise ValueError("Duplicate corpus ID")
                    seen.add(doc)
                    target.write(json.dumps({"id": doc, "title": row["title"] or "", "abstract": row["abstract"] or ""}, ensure_ascii=False) + "\n")
        if len(seen) != 64183 or any(not q["corpusids"] or not set(q["corpusids"]) <= seen for q in queries):
            raise ValueError("Unresolved judgments or unexpected corpus count")
        for name, rows in (("queries.jsonl", selected), ("development-queries.jsonl", development)):
            (stage / name).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8", newline="\n")
        (stage / "qrels.tsv").write_text("query-id\tcorpus-id\tscore\n" + "".join(
            f'{q["id"]}\t{doc}\t1\n' for q in selected for doc in sorted(set(q["corpusids"]))), encoding="utf-8", newline="\n")
        profile = {"schema_version": 1, "source": manifest, "selection_seed": SEED,
                   "selection": "Hamilton quotas by query_set/specificity; SHA256(seed:row-ID)",
                   "query_ids": [q["id"] for q in selected], "queries": len(selected), "corpus_documents": len(seen),
                   "development_queries": len(development), "related_queries_held_out": len(queries)-len(selected)-len(development),
                   "judgments": "inherited positives; other documents unjudged", "language": "en",
                   "representation": "title + U+0020 + abstract", "relevance_unit": "article",
                   "files": {p.name: digest(p) for p in stage.iterdir()}, "code_sha256": digest(Path(__file__))}
        (stage / "collection-manifest.json").write_text(json.dumps(profile, indent=2)+"\n", encoding="utf-8")
        os.rename(stage, output)
    return {key: profile[key] for key in ("queries", "corpus_documents", "development_queries", "related_queries_held_out")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download:
        from scifact_embeddings import download
        pinned = json.loads((Path(__file__).parents[1] / "config/litsearch-source.json").read_text())
        args.source.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(args.source).free < 3 * sum(row["bytes"] for row in pinned["files"]):
            raise OSError("Insufficient disk for dataset and transformed artifacts")
        source_manifest = args.source / "source-manifest.json"
        if source_manifest.exists() and json.loads(source_manifest.read_text()) != pinned:
            raise ValueError("Existing source directory belongs to another dataset profile")
        source_manifest.write_text(json.dumps(pinned, indent=2)+"\n", encoding="utf-8")
        for row in pinned["files"]:
            target = args.source / row["path"]
            target.parent.mkdir(parents=True, exist_ok=True)
            download(row["url"], target, row["sha256"], row["bytes"])
    print(json.dumps(prepare(args.source, args.output)))
