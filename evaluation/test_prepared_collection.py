"""Prepared JSON-lines collections, the format of LitSearch after prepare_litsearch.py."""

from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from search_retrieval.experiment.manifests import ManifestError
from embed import ROOT, iterate_documents, iterate_queries
import prepared_collection


CORPUS = [{"id": "11", "title": "Attention", "abstract": "Transformers."}, {"id": "7", "title": None, "abstract": "Untitled."}]
TEST = [{"id": "litsearch-0001", "query": "papers on attention", "corpusids": ["11"]}]
DEV = [{"id": "litsearch-0002", "query": "untitled work", "corpusids": ["7", "11"]}]


def lines(rows) -> bytes:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8")


class PreparedCollectionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.data = Path(directory.name)
        root = self.data / "litsearch"
        root.mkdir()
        files = {"corpus.jsonl": lines(CORPUS), "queries.jsonl": lines(TEST), "development-queries.jsonl": lines(DEV),
                 "qrels.tsv": b"query-id\tcorpus-id\tscore\nlitsearch-0001\t11\t1\n"}
        for name, data in files.items():
            (root / name).write_bytes(data)
        digest = {name: sha256(data).hexdigest() for name, data in files.items()}
        self.corpus = {"format": "prepared-jsonl", "documents": 2, "directory": "litsearch",
                       "corpus_file": "corpus.jsonl", "documents_sha256": digest["corpus.jsonl"]}
        self.test = {"queries_file": "queries.jsonl", "queries_sha256": digest["queries.jsonl"],
                     "qrels_file": "qrels.tsv", "qrels_sha256": digest["qrels.tsv"]}
        self.dev = {"queries_file": "development-queries.jsonl", "queries_sha256": digest["development-queries.jsonl"],
                    "qrels_file": "development-queries.jsonl", "qrels_sha256": digest["development-queries.jsonl"]}

    def test_documents_queries_and_both_judgment_forms(self):
        self.assertEqual(list(prepared_collection.documents(self.corpus, self.data)),
                         [("11", "Attention", "Transformers."), ("7", "", "Untitled.")])
        self.assertEqual(prepared_collection.queries(self.corpus, self.test, self.data), {"litsearch-0001": "papers on attention"})
        self.assertEqual(prepared_collection.judgments(self.corpus, self.test, self.data), {"litsearch-0001": {"11": 1}})
        self.assertEqual(prepared_collection.judgments(self.corpus, self.dev, self.data), {"litsearch-0002": {"7": 1, "11": 1}})

    def test_embedding_reads_title_and_abstract_as_separate_fields(self):
        transform = {"configuration": {"document": "title-text-fields"}, "chunking": {"policy": "identity"}}
        self.assertEqual(list(iterate_documents(self.corpus, transform, self.data)),
                         [("11", "Attention", "Transformers."), ("7", "", "Untitled.")])
        self.assertEqual(list(iterate_queries(self.dev, self.corpus, self.data)), [("litsearch-0002", "", "untitled work")])

    def test_changed_files_and_counts_are_rejected(self):
        (self.data / "litsearch/qrels.tsv").write_bytes(b"query-id\tcorpus-id\tscore\nlitsearch-0001\t7\t1\n")
        with self.assertRaisesRegex(ManifestError, "does not match"):
            prepared_collection.judgments(self.corpus, self.test, self.data)
        transform = {"configuration": {"document": "title-text-fields"}, "chunking": {"policy": "identity"}}
        with self.assertRaisesRegex(ManifestError, "count"):
            list(iterate_documents({**self.corpus, "documents": 3}, transform, self.data))
        with self.assertRaisesRegex(ManifestError, "requires"):
            list(prepared_collection.documents({"format": "prepared-jsonl"}, self.data))

    def test_litsearch_manifests_pin_the_archived_preparation(self):
        evidence = json.loads((ROOT / "evaluation/evidence/scientific-pdf-v1/litsearch-collection.json").read_bytes())
        corpus = json.loads((ROOT / "manifests/corpus/litsearch.json").read_bytes())["body"]
        test = json.loads((ROOT / "manifests/queries/litsearch-test.json").read_bytes())["body"]
        dev = json.loads((ROOT / "manifests/queries/litsearch-dev.json").read_bytes())["body"]
        self.assertEqual((corpus["documents"], test["queries"], dev["queries"]),
                         (evidence["corpus_documents"], evidence["queries"], evidence["development_queries"]))
        self.assertEqual(corpus["documents_sha256"], evidence["files"]["corpus.jsonl"])
        self.assertEqual((test["queries_sha256"], test["qrels_sha256"]),
                         (evidence["files"]["queries.jsonl"], evidence["files"]["qrels.tsv"]))
        self.assertEqual(dev["queries_sha256"], evidence["files"]["development-queries.jsonl"])
        self.assertEqual((test["split"], dev["split"]), ("test", "dev"))
