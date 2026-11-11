"""Execute one hashed run configuration and write its reproducible artifacts.

    python evaluation/bench.py run CONFIG --data-dir DIR [--output DIR]
    python evaluation/bench.py report CONFIG --frozen-dev DEV_CONFIG --data-dir DIR [--output DIR]
    python evaluation/bench.py verify OUTPUT [--config CONFIG]
    python evaluation/bench.py seal MANIFEST

`run` accepts the dev and regression splits. Only `report` reads test judgments.
"""

import argparse
from csv import DictReader
from hashlib import file_digest, sha256
from io import StringIO
import json
from pathlib import Path
from statistics import fmean
import sys
from time import perf_counter
from zipfile import ZipFile

from search_retrieval.experiment.manifests import ManifestError, inside, seal
from search_retrieval.experiment.outputs import verify_outputs, write_run
from search_retrieval.experiment.provenance import (
    ProvenanceError, authorize_split, observed_runtime, verify_provenance,
)
from search_retrieval.experiment.run_config import ConfigError, ResolvedRun, resolve
from scifact_bm25 import bm25


ROOT = Path(__file__).resolve().parents[1]
MEASURES = {"ndcg_cut.10", "recall.10,100"}
BM25_REFERENCE = {"k1": 1.2, "b": 0.75, "tokenizer": "unicode-word-casefold"}
TITLE_SPACE_TEXT = {"document": "title-space-text"}


def _member(archive: ZipFile, name: str, expected: str | None = None) -> bytes:
    data = archive.read(name)
    if expected is not None and sha256(data).hexdigest() != expected:
        raise ManifestError(f"Archive member does not match its manifest: {name}")
    return data


def _archive(resolved: ResolvedRun, data_dir: Path) -> Path:
    corpus = resolved.corpus
    if corpus["format"] != "beir-zip":
        raise ConfigError(f"Unsupported corpus format: {corpus['format']}")
    path = data_dir / corpus["archive"]
    with path.open("rb") as source:
        if file_digest(source, "sha256").hexdigest() != corpus["archive_sha256"]:
            raise ManifestError("Corpus archive does not match its manifest")
    return path


def load_corpus_and_queries(resolved: ResolvedRun, data_dir: Path):
    """Read documents and query text. Judgments are loaded separately, after authorization."""
    if resolved.transform.configuration != TITLE_SPACE_TEXT or resolved.transform.chunking != {"policy": "identity"}:
        raise ConfigError("Unsupported transform for a BEIR passage corpus")
    queries_manifest = resolved.query_set_manifest
    with ZipFile(_archive(resolved, data_dir)) as archive:
        documents = _member(archive, resolved.corpus["corpus_member"])
        queries = _member(archive, queries_manifest["queries_member"], queries_manifest["queries_sha256"])
    corpus = {doc["_id"]: doc.get("title", "") + " " + doc["text"]
              for doc in map(json.loads, documents.splitlines())}
    if len(corpus) != resolved.corpus["documents"]:
        raise ManifestError("Corpus document count does not match its manifest")
    return corpus, {query["_id"]: query["text"] for query in map(json.loads, queries.splitlines())}


def load_judgments(resolved: ResolvedRun, data_dir: Path, corpus, queries):
    manifest = resolved.query_set_manifest
    with ZipFile(_archive(resolved, data_dir)) as archive:
        rows = _member(archive, manifest["qrels_member"], manifest["qrels_sha256"]).decode("utf-8")
    judgments: dict[str, dict[str, int]] = {}
    for row in DictReader(StringIO(rows), delimiter="\t"):
        judgments.setdefault(row["query-id"], {})[row["corpus-id"]] = int(row["score"])
    if (len(judgments) != manifest["queries"] or not judgments.keys() <= queries.keys()
            or any(not labels.keys() <= corpus.keys() for labels in judgments.values())):
        raise ManifestError("Judgments do not match the corpus, the queries or the declared count")
    return judgments


def bm25_reference(resolved: ResolvedRun, corpus, queries):
    """The in-process lexical reference. Its parameters are fixed in `scifact_bm25.bm25`."""
    index, ranking = resolved.config["index"], resolved.config["ranking"]
    if (index["engine"] != "in-process" or index.get("lexical") != BM25_REFERENCE
            or set(index) - {"engine", "schema_revision", "lexical"}
            or ranking["target_hits"] != 100 or ranking["fusion"] != {"kind": "none"} or resolved.spaces):
        raise ConfigError("bm25-reference runs in-process with k1=1.2, b=0.75, depth 100 and no fusion or space")
    return {qid: list(hits.items()) for qid, hits in bm25(corpus, queries).items()}, "not-applicable"


SYSTEMS = {"bm25-reference": bm25_reference}


def evaluate(rankings, judgments):
    import pytrec_eval

    # Reciprocal ranks keep the ranker's own tie break when the evaluator sorts by score.
    ordered = {qid: {doc: 1 / rank for rank, (doc, _) in enumerate(hits, 1)} for qid, hits in rankings.items()}
    scores = pytrec_eval.RelevanceEvaluator(judgments, MEASURES).evaluate(ordered)
    if scores.keys() != judgments.keys():
        raise ValueError("The evaluator did not score every judged query")
    return scores


def execute(command: str, config: str, frozen_dev: str | None, data_dir: Path, output: Path | None) -> dict:
    resolved = resolve(ROOT, config)
    verify_provenance(ROOT, resolved)
    authorize_split(ROOT, command, resolved, resolve(ROOT, frozen_dev) if frozen_dev else None)
    environment = observed_runtime(ROOT, resolved.runtime)
    profile = resolved.config["ranking"]["profile"]
    if profile not in SYSTEMS:
        raise ConfigError(f"No system implements the ranking profile {profile}")
    timings = {}
    started = perf_counter()
    corpus, queries = load_corpus_and_queries(resolved, data_dir)
    judgments = load_judgments(resolved, data_dir, corpus, queries)
    timings["load_ms"] = (perf_counter() - started) * 1000
    started = perf_counter()
    rankings, strategy = SYSTEMS[profile](resolved, corpus, {qid: queries[qid] for qid in sorted(judgments)})
    timings["rank_ms"] = (perf_counter() - started) * 1000
    if rankings.keys() != judgments.keys():
        raise ValueError("The system did not answer every query")
    scores = evaluate(rankings, judgments)
    records = [
        {"qid": qid, "generation": resolved.generation.digest, "ann_strategy": strategy,
         "terminal_state": "complete", "returned": len(rankings[qid]), "metrics": scores[qid]}
        for qid in sorted(rankings)
    ]
    metrics = {name: fmean(row[name] for row in scores.values()) for name in sorted(next(iter(scores.values())))}
    if output is None:
        output = ROOT / "evaluation/runs" / f"{resolved.config['run']['id']}-{resolved.run.digest[:12]}"
    manifest = write_run(output, resolved, command, rankings, records, metrics, environment, timings)
    return {"output": str(output), "run": resolved.run.digest, "metrics": metrics, "outputs": manifest["outputs"]}


def reseal(relative: str) -> dict:
    path = inside(ROOT, relative)
    document = json.loads(path.read_bytes())
    sealed = seal(document["kind"], document["body"])
    path.write_bytes((json.dumps(sealed, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return {"manifest": relative, "sha256": sealed["sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "report"):
        command = commands.add_parser(name)
        command.add_argument("config")
        command.add_argument("--data-dir", type=Path, required=True)
        command.add_argument("--output", type=Path)
        if name == "report":
            command.add_argument("--frozen-dev", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("output", type=Path)
    verify.add_argument("--config")
    commands.add_parser("seal").add_argument("manifest")
    args = parser.parse_args()
    try:
        if args.command == "seal":
            result = reseal(args.manifest)
        elif args.command == "verify":
            manifest = verify_outputs(args.output)
            if args.config and resolve(ROOT, args.config).run.digest != manifest["keys"]["run"]:
                raise ConfigError("The configuration resolves to a different RunKey")
            result = {"verified": str(args.output), "run": manifest["keys"]["run"]}
        else:
            result = execute(args.command, args.config, getattr(args, "frozen_dev", None), args.data_dir, args.output)
    except (ConfigError, ManifestError, ProvenanceError, ValueError, OSError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
