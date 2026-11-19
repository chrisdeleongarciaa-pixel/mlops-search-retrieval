"""Execute one hashed run configuration and write its reproducible artifacts.

    python evaluation/bench.py run CONFIG --data-dir DIR [--output DIR]
    python evaluation/bench.py report CONFIG --frozen-dev DEV_CONFIG --data-dir DIR [--output DIR]
    python evaluation/bench.py regress [--fast] --data-dir DIR
    python evaluation/bench.py verify OUTPUT [--config CONFIG]
    python evaluation/bench.py seal MANIFEST
    python evaluation/bench.py digest-index DIRECTORY

`run` accepts the dev and regression splits. Only `report` reads test judgments.
Anserini runs need BENCH_JAVA (the java executable) and BENCH_ANSERINI_JAR.
"""

import argparse
from csv import DictReader
from hashlib import file_digest, sha256
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter
import tomllib
from zipfile import ZipFile

from search_retrieval.experiment.manifests import ManifestError, inside, seal
from search_retrieval.experiment.outputs import read_trec_run, verify_outputs, write_run
from search_retrieval.experiment.provenance import (
    ProvenanceError, authorize_split, observed_runtime, verify_provenance,
)
from search_retrieval.experiment.run_config import ConfigError, ResolvedRun, resolve
from search_retrieval.experiment.textmode import require_utf8_mode, rerun_in_utf8_mode
from measures import aggregate, evaluate
import prepared_collection
from scifact_bm25 import bm25
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
REGRESSION = "config/runs/regression.toml"
BATCH = 500
BM25_REFERENCE = {"k1": 1.2, "b": 0.75, "tokenizer": "unicode-word-casefold"}
TITLE_SPACE_TEXT = {"document": "title-space-text"}
PASSAGE_IDENTITY = {"document": "passage"}


def _fields(body: dict, kind: str, *names: str) -> None:
    missing = [name for name in names if name not in body]
    if missing:
        raise ManifestError(f"{kind} manifest of this format requires {missing}")


def _file_sha256(path: Path) -> str:
    with path.open("rb") as source:
        return file_digest(source, "sha256").hexdigest()


def directory_digest(directory: Path) -> str:
    """Digest of every file name, size and content under a directory."""
    lines = []
    for path in sorted(item for item in directory.rglob("*") if item.is_file()):
        lines.append(f"{path.relative_to(directory).as_posix()}\t{path.stat().st_size}\t{_file_sha256(path)}\n")
    if not lines:
        raise ManifestError(f"Empty or missing directory: {directory}")
    return sha256("".join(lines).encode("utf-8")).hexdigest()


class BeirZip:
    """A BEIR archive: corpus, queries and judgments are members of one pinned zip file."""

    def __init__(self, resolved: ResolvedRun, data_dir: Path):
        self.corpus_manifest, self.query_set = resolved.corpus, resolved.query_set_manifest
        _fields(self.corpus_manifest, "corpus", "archive", "archive_sha256", "corpus_member")
        _fields(self.query_set, "query-set", "queries_member", "qrels_member")
        if resolved.transform.configuration != TITLE_SPACE_TEXT or resolved.transform.chunking != {"policy": "identity"}:
            raise ConfigError("Unsupported transform for a BEIR passage corpus")
        self.path = data_dir / self.corpus_manifest["archive"]
        if _file_sha256(self.path) != self.corpus_manifest["archive_sha256"]:
            raise ManifestError("Corpus archive does not match its manifest")
        with ZipFile(self.path) as archive:
            documents = archive.read(self.corpus_manifest["corpus_member"])
            queries = archive.read(self.query_set["queries_member"])
        if sha256(queries).hexdigest() != self.query_set["queries_sha256"]:
            raise ManifestError("Queries do not match the query-set manifest")
        self.corpus = {doc["_id"]: doc.get("title", "") + " " + doc["text"]
                       for doc in map(json.loads, documents.splitlines())}
        if len(self.corpus) != self.corpus_manifest["documents"]:
            raise ManifestError("Corpus document count does not match its manifest")
        self.queries = {query["_id"]: query["text"] for query in map(json.loads, queries.splitlines())}

    def judgments(self) -> dict[str, dict[str, int]]:
        with ZipFile(self.path) as archive:
            rows = archive.read(self.query_set["qrels_member"])
        if sha256(rows).hexdigest() != self.query_set["qrels_sha256"]:
            raise ManifestError("Judgments do not match the query-set manifest")
        judgments: dict[str, dict[str, int]] = {}
        for row in DictReader(StringIO(rows.decode("utf-8")), delimiter="\t"):
            judgments.setdefault(row["query-id"], {})[row["corpus-id"]] = int(row["score"])
        if any(not labels.keys() <= self.corpus.keys() for labels in judgments.values()):
            raise ManifestError("Judgments name documents outside the corpus")
        return judgments


class AnseriniIndex:
    """A prebuilt Lucene index with queries and judgments from ir_datasets."""

    def __init__(self, resolved: ResolvedRun, data_dir: Path):
        self.corpus_manifest, self.query_set = resolved.corpus, resolved.query_set_manifest
        _fields(self.corpus_manifest, "corpus", "index_directory", "index_sha256")
        _fields(self.query_set, "query-set", "ir_datasets")
        if resolved.transform.configuration != PASSAGE_IDENTITY or resolved.transform.chunking != {"policy": "identity"}:
            raise ConfigError("Unsupported transform for a prebuilt passage index")
        self.index = data_dir / "anserini" / self.corpus_manifest["index_directory"]
        if directory_digest(self.index) != self.corpus_manifest["index_sha256"]:
            raise ManifestError("Index does not match its corpus manifest")
        os.environ["IR_DATASETS_HOME"] = str((data_dir / "ir_datasets").resolve())
        require_utf8_mode("ir_datasets")
        import ir_datasets

        self.dataset = ir_datasets.load(self.query_set["ir_datasets"])
        self.corpus = None
        self.queries = {}
        digest = sha256()
        for query in self.dataset.queries_iter():
            digest.update(f"{query.query_id}\t{query.text}\n".encode("utf-8"))
            self.queries[query.query_id] = query.text
        if digest.hexdigest() != self.query_set["queries_sha256"]:
            raise ManifestError("Queries do not match the query-set manifest")

    def judgments(self) -> dict[str, dict[str, int]]:
        digest = sha256()
        judgments: dict[str, dict[str, int]] = {}
        for row in self.dataset.qrels_iter():
            digest.update(f"{row.query_id}\t{row.iteration}\t{row.doc_id}\t{row.relevance}\n".encode("utf-8"))
            judgments.setdefault(row.query_id, {})[row.doc_id] = int(row.relevance)
        if digest.hexdigest() != self.query_set["qrels_sha256"]:
            raise ManifestError("Judgments do not match the query-set manifest")
        return judgments


class PreparedCollection:
    """A collection materialized as JSON lines, pinned file by file (see prepared_collection.py)."""

    def __init__(self, resolved: ResolvedRun, data_dir: Path):
        self.corpus_manifest, self.query_set, self.data_dir = resolved.corpus, resolved.query_set_manifest, data_dir
        if resolved.transform.chunking != {"policy": "identity"} or resolved.transform.configuration not in (
                TITLE_SPACE_TEXT, {"document": "title-text-fields"}):
            raise ConfigError("Unsupported transform for a prepared title and abstract corpus")
        self.corpus = {item: title + " " + abstract
                       for item, title, abstract in prepared_collection.documents(self.corpus_manifest, data_dir)}
        if len(self.corpus) != self.corpus_manifest["documents"]:
            raise ManifestError("Corpus document count does not match its manifest")
        self.queries = prepared_collection.queries(self.corpus_manifest, self.query_set, data_dir)

    def judgments(self) -> dict[str, dict[str, int]]:
        judgments = prepared_collection.judgments(self.corpus_manifest, self.query_set, self.data_dir)
        if any(not labels.keys() <= self.corpus.keys() for labels in judgments.values()):
            raise ManifestError("Judgments name documents outside the corpus")
        return judgments


FORMATS = {"beir-zip": BeirZip, "anserini-lucene-index": AnseriniIndex,
           prepared_collection.FORMAT: PreparedCollection}


def bm25_reference(resolved: ResolvedRun, dataset, queries: dict[str, str], work: Path):
    """The in-process lexical reference. Its parameters are fixed in `scifact_bm25.bm25`."""
    index, ranking = resolved.config["index"], resolved.config["ranking"]
    if (dataset.corpus is None or index["engine"] != "in-process" or index.get("lexical") != BM25_REFERENCE
            or set(index) - {"engine", "schema_revision", "lexical"}
            or ranking["target_hits"] != 100 or ranking["fusion"] != {"kind": "none"} or resolved.spaces):
        raise ConfigError("bm25-reference runs in-process with k1=1.2, b=0.75, depth 100 and no fusion or space")
    results = bm25(dataset.corpus, queries)
    return ((qid, list(results[qid].items())) for qid in queries), "not-applicable", {}


def answer_every_query(queries, produced):
    """Merge engine output with the sorted query list. A query the engine omitted has no hits."""
    produced = iter(produced)
    item = next(produced, None)
    for qid in queries:
        if item is not None and item[0] == qid:
            yield item
            item = next(produced, None)
        else:
            yield qid, []
    if item is not None:
        raise ValueError(f"The engine returned an unknown or out-of-order query: {item[0]}")


def anserini_tools(resolved: ResolvedRun) -> tuple[str, str, dict]:
    """Check the Java runtime and the Anserini jar against the runtime manifest."""
    declared = resolved.runtime.get("tools", {})
    if set(declared) != {"java", "anserini_jar_sha256"}:
        raise ConfigError("An Anserini run needs runtime tools: java and anserini_jar_sha256")
    try:
        java, jar = os.environ["BENCH_JAVA"], os.environ["BENCH_ANSERINI_JAR"]
    except KeyError as error:
        raise ProvenanceError(f"Set {error.args[0]} for an Anserini run") from error
    try:
        banner = subprocess.run([java, "-version"], capture_output=True, text=True, check=True).stderr
    except (OSError, subprocess.CalledProcessError) as error:
        raise ProvenanceError("The Java runtime did not start") from error
    if f'version "{declared["java"]}"' not in banner:
        raise ProvenanceError(f"Java is not version {declared['java']}: {banner.splitlines()[0]}")
    if _file_sha256(Path(jar)) != declared["anserini_jar_sha256"]:
        raise ProvenanceError("The Anserini jar does not match the runtime manifest")
    return java, jar, {"java": declared["java"], "anserini_jar_sha256": declared["anserini_jar_sha256"]}


def anserini_bm25(resolved: ResolvedRun, dataset, queries: dict[str, str], work: Path):
    """BM25 over a prebuilt Lucene index, executed by the pinned Anserini jar."""
    index, ranking = resolved.config["index"], resolved.config["ranking"]
    lexical = index.get("lexical", {})
    if (not isinstance(dataset, AnseriniIndex) or index["engine"] != "anserini"
            or set(index) - {"engine", "schema_revision", "lexical"} or set(lexical) != {"k1", "b"}
            or not all(type(value) is float for value in lexical.values())
            or index["schema_revision"] != dataset.corpus_manifest["index_directory"]
            or ranking["fusion"] != {"kind": "none"} or resolved.spaces):
        raise ConfigError("anserini-bm25 needs engine anserini, the index directory as schema_revision, "
                          "float k1 and b, and no fusion or space")
    java, jar, tools = anserini_tools(resolved)
    if any("\t" in text or "\n" in text or not text.strip() for text in queries.values()):
        raise ValueError("Query text must be a nonempty single line without tabs")
    topics, output = work / "topics.tsv", work / "anserini.trec"
    topics.write_bytes("".join(f"{qid}\t{text}\n" for qid, text in queries.items()).encode("utf-8"))
    threads = os.environ.get("BENCH_THREADS", "2")
    command = [java, "-cp", jar, "io.anserini.search.SearchCollection", "-index", str(dataset.index),
               "-topics", str(topics), "-topicReader", "TsvString", "-output", str(output),
               "-bm25", "-bm25.k1", repr(lexical["k1"]), "-bm25.b", repr(lexical["b"]),
               "-hits", str(ranking["target_hits"]), "-threads", threads]
    completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if completed.returncode or not output.is_file():
        raise RuntimeError(f"Anserini failed: {(completed.stderr or completed.stdout)[-2000:]}")

    def stream():
        with output.open("r", encoding="utf-8") as lines:
            yield from answer_every_query(queries, read_trec_run(lines))

    return stream(), "not-applicable", {"tools": tools, "threads": int(threads)}


class QueriesOnly:
    """Queries and judgments from ir_datasets for a corpus served by an engine (MS MARCO text)."""

    def __init__(self, resolved: ResolvedRun, data_dir: Path):
        self.corpus_manifest, self.query_set, self.corpus = resolved.corpus, resolved.query_set_manifest, None
        _fields(self.query_set, "query-set", "ir_datasets")
        os.environ["IR_DATASETS_HOME"] = str((data_dir / "ir_datasets").resolve())
        require_utf8_mode("ir_datasets")
        import ir_datasets

        self.dataset = ir_datasets.load(self.query_set["ir_datasets"])
        digest, self.queries = sha256(), {}
        for query in self.dataset.queries_iter():
            digest.update(f"{query.query_id}\t{query.text}\n".encode("utf-8"))
            self.queries[query.query_id] = query.text
        if digest.hexdigest() != self.query_set["queries_sha256"]:
            raise ManifestError("Queries do not match the query-set manifest")

    judgments = AnseriniIndex.judgments


FORMATS["ir-datasets-docs"] = QueriesOnly
VESPA_FUSIONS = {"none", "rrf", "convex"}


def vespa_projection(resolved: ResolvedRun) -> tuple[dict, object]:
    """The projection named by [index].schema_revision, checked against the run's corpus and space."""
    from search_retrieval.adapters.vespa_passages import VespaPassages
    from search_retrieval.experiment.manifests import parse_manifest

    try:
        url, root = os.environ["BENCH_VESPA_URL"], Path(os.environ["BENCH_PROJECTIONS_DIR"])
    except KeyError as error:
        raise ProvenanceError(f"Set {error.args[0]} for a Vespa run") from error
    label = resolved.config["index"]["schema_revision"]
    projection, _ = parse_manifest((root / label / "manifest.json").read_bytes(), "vespa-projection")
    if projection["collection"] != label or projection["corpus"] != resolved.corpus_digest:
        raise ConfigError("The projection does not hold this run's corpus")
    spaces = {space.digest for space in resolved.spaces.values()}
    if spaces and spaces != {projection["space"]}:
        raise ConfigError("The projection holds another vector space")
    return projection, VespaPassages(url, timeout=120)


def query_vectors(resolved: ResolvedRun, queries: dict[str, str]):
    """Canonical query rows of the run's space, by query ID."""
    from canonical import CanonicalRows
    from embed import set_directory
    from vector_store import Identity

    if len(resolved.spaces) != 1:
        raise ConfigError("A dense Vespa run names exactly one space")
    space = next(iter(resolved.spaces.values()))
    try:
        vectors_dir = Path(os.environ["BENCH_VECTORS_DIR"])
    except KeyError as error:
        raise ProvenanceError("Set BENCH_VECTORS_DIR for a dense Vespa run") from error
    rows = CanonicalRows(set_directory(vectors_dir, space.digest, Identity(
        space, "query", {"queries": resolved.query_set_manifest["queries_sha256"]})))
    index = {item: position for position, item in enumerate(rows.ids)}
    missing = queries.keys() - index.keys()
    if missing:
        raise ConfigError(f"The query vector set lacks {len(missing)} queries")
    return {qid: rows.take(np.array([index[qid]]), np.float32)[0].tolist() for qid in queries}, rows.digest


def vespa_system(resolved: ResolvedRun, dataset, queries: dict[str, str], work: Path):
    """BM25, dense or fused retrieval from a Vespa projection. Branches are read at the run depth."""
    from search_retrieval.query.ranking import Ranked, convex, fuse

    index, ranking = resolved.config["index"], resolved.config["ranking"]
    profile, fusion, depth = ranking["profile"], ranking["fusion"], ranking["target_hits"]
    lexical = index.get("lexical", {})
    if index["engine"] != "vespa" or set(index) - {"engine", "schema_revision", "lexical"} or fusion["kind"] not in VESPA_FUSIONS:
        raise ConfigError("A Vespa run names engine vespa, the collection label and a known fusion")
    if (profile == "vespa-hybrid") != (fusion["kind"] != "none"):
        raise ConfigError("Only vespa-hybrid fuses, and it must name its fusion")
    if profile in ("vespa-bm25", "vespa-hybrid") and set(lexical) != {"profile"}:
        raise ConfigError("BM25 runs name the rank profile in [index].lexical")
    _, client = vespa_projection(resolved)
    vectors, vectors_digest = query_vectors(resolved, queries) if profile != "vespa-bm25" else (None, None)
    explore = int(fusion.get("explore", 0)) if profile == "vespa-hybrid" else 0

    def branch_lexical(qid):
        return [Ranked(hit.doc_id, hit.score) for hit in client.lexical(index["schema_revision"], queries[qid], depth,
                                                                       profile=lexical["profile"])]

    def branch_dense(qid):
        hits, _ = client.dense(index["schema_revision"], vectors[qid], depth, approximate=True, explore=explore)
        return [Ranked(hit.doc_id, hit.score) for hit in hits]

    def stream():
        for qid in queries:
            if profile == "vespa-bm25":
                hits = branch_lexical(qid)
            elif profile == "vespa-dense":
                hits = branch_dense(qid)
            elif fusion["kind"] == "rrf":
                if set(fusion) - {"kind", "k", "explore"} or fusion.get("k", 60) != 60:
                    raise ConfigError("RRF uses k = 60, the constant of the existing baseline")
                hits = list(fuse(branch_lexical(qid), branch_dense(qid), depth=depth).fused)
            else:
                if set(fusion) - {"kind", "alpha", "normalization", "explore"} or fusion.get("normalization") != "minmax":
                    raise ConfigError("Convex fusion names alpha and normalization = minmax")
                hits = list(convex(branch_lexical(qid), branch_dense(qid), alpha=float(fusion["alpha"]), depth=depth).fused)
            ordered = sorted(hits, key=lambda hit: (-hit.score, hit.id))[:depth]
            yield qid, [(hit.id, float(hit.score)) for hit in ordered]

    extra = {"query_vectors": vectors_digest} if vectors_digest else {}
    return stream(), "engine-reported" if profile != "vespa-bm25" else "not-applicable", extra


SYSTEMS = {"bm25-reference": bm25_reference, "anserini-bm25": anserini_bm25,
           "vespa-bm25": vespa_system, "vespa-dense": vespa_system, "vespa-hybrid": vespa_system}


def execute(command: str, config: str, frozen_dev: str | None, data_dir: Path, output: Path | None) -> dict:
    resolved = resolve(ROOT, config)
    verify_provenance(ROOT, resolved)
    authorize_split(ROOT, command, resolved, resolve(ROOT, frozen_dev) if frozen_dev else None)
    environment = observed_runtime(ROOT, resolved.runtime)
    profile = resolved.config["ranking"]["profile"]
    if profile not in SYSTEMS:
        raise ConfigError(f"No system implements the ranking profile {profile}")
    if resolved.corpus["format"] not in FORMATS:
        raise ConfigError(f"Unsupported corpus format: {resolved.corpus['format']}")
    timings = {}
    started = perf_counter()
    dataset = FORMATS[resolved.corpus["format"]](resolved, data_dir)
    judgments = dataset.judgments()
    if len(judgments) != resolved.query_set_manifest["queries"] or not judgments.keys() <= dataset.queries.keys():
        raise ManifestError("Judgments do not match the queries or the declared count")
    queries = {qid: dataset.queries[qid] for qid in sorted(judgments)}
    timings["load_ms"] = (perf_counter() - started) * 1000
    if output is None:
        output = ROOT / "evaluation/runs" / f"{resolved.config['run']['id']}-{resolved.run.digest[:12]}"
    scores: dict[str, dict[str, float]] = {}
    with TemporaryDirectory(ignore_cleanup_errors=True) as work:
        started = perf_counter()
        results, strategy, extra = SYSTEMS[profile](resolved, dataset, queries, Path(work))
        recorded = ("tools", "query_vectors")
        environment = {**environment, **{name: value for name, value in extra.items() if name in recorded}}
        timings.update({name: value for name, value in extra.items() if name not in recorded})

        def records():
            batch = []
            for item in results:
                batch.append(item)
                if len(batch) == BATCH:
                    yield from scored(batch)
                    batch = []
            yield from scored(batch)

        def scored(batch):
            rows = evaluate(dict(batch), judgments) if batch else {}
            scores.update(rows)
            for qid, hits in batch:
                yield qid, hits, {
                    "qid": qid, "generation": resolved.generation.digest, "ann_strategy": strategy,
                    "terminal_state": "complete", "returned": len(hits), "metrics": rows[qid],
                }

        def summarize():
            if scores.keys() != queries.keys():
                raise ValueError("The system did not answer every query")
            timings["rank_and_score_ms"] = (perf_counter() - started) * 1000
            return aggregate(scores), timings

        manifest = write_run(output, resolved, command, records(), environment, summarize)
    return {"output": str(output), "run": resolved.run.digest, "metrics": manifest["metrics"],
            "outputs": manifest["outputs"]}


def regress(fast: bool, data_dir: Path) -> dict:
    """Execute each registered reference run and compare one measure with its expected value."""
    suite = tomllib.loads(inside(ROOT, REGRESSION).read_text(encoding="utf-8"))
    checks = [check for check in suite["check"] if check["fast"] or not fast]
    results = []
    (ROOT / "evaluation/runs").mkdir(exist_ok=True)
    for check in checks:
        with TemporaryDirectory(dir=ROOT / "evaluation/runs", ignore_cleanup_errors=True) as work:
            outcome = execute("run", check["config"], None, data_dir, Path(work) / "run")
        observed = outcome["metrics"][check["measure"]]
        results.append({"name": check["name"], "measure": check["measure"], "expected": check["expected"],
                        "tolerance": check["tolerance"], "observed": observed,
                        "passed": abs(observed - check["expected"]) <= check["tolerance"]})
    return {"checks": results, "skipped": len(suite["check"]) - len(checks),
            "passed": bool(results) and all(result["passed"] for result in results)}


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
    regression = commands.add_parser("regress")
    regression.add_argument("--fast", action="store_true")
    regression.add_argument("--data-dir", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("output", type=Path)
    verify.add_argument("--config")
    commands.add_parser("seal").add_argument("manifest")
    commands.add_parser("digest-index").add_argument("directory", type=Path)
    args = parser.parse_args()
    rerun = rerun_in_utf8_mode()
    if rerun is not None:
        return rerun
    try:
        if args.command == "seal":
            result = reseal(args.manifest)
        elif args.command == "digest-index":
            result = {"directory": str(args.directory), "sha256": directory_digest(args.directory)}
        elif args.command == "verify":
            manifest = verify_outputs(args.output)
            if args.config and resolve(ROOT, args.config).run.digest != manifest["keys"]["run"]:
                raise ConfigError("The configuration resolves to a different RunKey")
            result = {"verified": str(args.output), "run": manifest["keys"]["run"]}
        elif args.command == "regress":
            result = regress(args.fast, args.data_dir)
        else:
            result = execute(args.command, args.config, getattr(args, "frozen_dev", None), args.data_dir, args.output)
    except (ConfigError, ManifestError, ProvenanceError, ValueError, OSError, RuntimeError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return int(args.command == "regress" and not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
