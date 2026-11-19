"""Run configuration, manifest resolution, provenance and the test-split lock.

Each case builds a throwaway Git repository so that commit ordering is real.
"""

from hashlib import sha256
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from search_retrieval.experiment import provenance, textmode
from search_retrieval.experiment.manifests import ManifestError, parse_manifest, read_manifest, seal
from search_retrieval.experiment.outputs import (
    parse_trec_run, read_trec_run, trec_run, verify_outputs, write_run,
)
from search_retrieval.experiment.provenance import (
    ProvenanceError, authorize_split, observed_runtime, verify_provenance,
)
from search_retrieval.experiment.run_config import ConfigError, parse_config, resolve


A, B = "a" * 64, "b" * 64
LOCK = b"example==1.0 --hash=sha256:00\n"

CONFIG = """
[run]
id = "fixture.bm25"
code_commit = "{commit}"
split = "{split}"

[corpus]
manifest = "manifests/corpus.json"

[transform]
manifest = "manifests/transform.json"

[index]
engine = "in-process"
schema_revision = "none"
lexical = {{ k1 = 1.2, b = 0.75 }}

[ranking]
profile = "bm25-reference"
target_hits = {hits}
fusion = {{ kind = "none" }}

[queries]
set = "fixture/{split}"
judgments = "human"
manifest = "manifests/queries-{split}.json"

[runtime]
manifest = "manifests/runtime.json"
"""


def git(root, *arguments):
    return subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
         "-c", "core.autocrlf=false", *arguments],
        cwd=root, check=True, capture_output=True, text=True,
    ).stdout.strip()


def write(root, relative, data):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))


def manifest(kind, body):
    return json.dumps(seal(kind, body), indent=2)


def query_set(split):
    return manifest("query-set", {
        "name": f"fixture/{split}", "split": split, "judgments": "human", "queries": 2,
        "queries_member": "q.jsonl", "queries_sha256": A, "qrels_member": f"{split}.tsv", "qrels_sha256": B,
    })


class Repository:
    """A repository whose first commit holds code and manifests; configurations come later."""

    def __init__(self, test_case):
        directory = TemporaryDirectory()
        test_case.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve() / "project"
        self.root.mkdir()
        git(self.root, "init", "-q", "-b", "main")
        write(self.root, "src/code.py", "x = 1\n")
        write(self.root, "locks/example.lock", LOCK)
        write(self.root, "manifests/corpus.json", manifest("corpus", {
            "name": "fixture", "source": "synthetic", "license": "none", "format": "beir-zip",
            "archive": "fixture.zip", "archive_sha256": A, "corpus_member": "corpus.jsonl", "documents": 3,
        }))
        write(self.root, "manifests/transform.json", manifest("transform", {
            "parser": "none", "parser_version": "0", "configuration": {"document": "title-space-text"},
            "chunking": {"policy": "identity"}, "tokenizer": "none",
        }))
        write(self.root, "manifests/runtime.json", manifest("runtime", {
            "python": "3.12.10", "platform": "win_amd64",
            "locks": {"locks/example.lock": sha256(LOCK).hexdigest()},
        }))
        for split in ("dev", "test", "regression"):
            write(self.root, f"manifests/queries-{split}.json", query_set(split))
        self.code = self.commit("code")

    def commit(self, message):
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD")

    def config(self, split, *, hits=100, commit=None, name=None):
        relative = f"config/runs/{name or split}.toml"
        write(self.root, relative, CONFIG.format(commit=commit or self.code, split=split, hits=hits))
        return relative


class ManifestTests(unittest.TestCase):
    def test_a_changed_value_no_longer_matches_the_digest(self):
        sealed = seal("corpus", {"documents": 3})
        self.assertEqual(parse_manifest(json.dumps(sealed).encode(), "corpus"), ({"documents": 3}, sealed["sha256"]))
        tampered = json.dumps({**sealed, "body": {"documents": 4}}).encode()
        with self.assertRaisesRegex(ManifestError, "does not match its digest"):
            parse_manifest(tampered, "corpus")

    def test_kind_shape_and_duplicate_keys_are_checked(self):
        sealed = seal("corpus", {"documents": 3})
        cases = (
            (json.dumps(sealed).encode(), "transform"),
            (json.dumps({**sealed, "extra": 1}).encode(), "corpus"),
            (json.dumps({**sealed, "schema_version": 2}).encode(), "corpus"),
            (b'{"kind":"corpus","kind":"corpus","schema_version":1,"body":{},"sha256":""}', "corpus"),
            (b"not json", "corpus"),
        )
        for data, kind in cases:
            with self.subTest(data=data[:40]), self.assertRaises(ManifestError):
                parse_manifest(data, kind)

    def test_references_stay_inside_the_repository(self):
        with TemporaryDirectory() as directory:
            root = Path(directory) / "project"
            root.mkdir()
            write(Path(directory), "outside.json", manifest("corpus", {}))
            for reference in ("../outside.json", str(Path(directory) / "outside.json"), "missing.json", ""):
                with self.subTest(reference=reference), self.assertRaises(ManifestError):
                    read_manifest(root, reference, "corpus")


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository(self)
        self.root = self.repository.root

    def test_table_and_key_order_do_not_change_the_run_key(self):
        first = resolve(self.root, self.repository.config("dev"))
        text = CONFIG.format(commit=self.repository.code, split="dev", hits=100)
        sections = ["[" + part for part in text.strip().split("\n[")]
        sections[0] = sections[0][1:]
        reordered = []
        for section in reversed(sections):
            header, *lines = [line for line in section.splitlines() if line]
            reordered.append("\n".join([header, *reversed(lines)]))
        write(self.root, "config/runs/reordered.toml", "\n\n".join(reordered) + "\n")
        second = resolve(self.root, "config/runs/reordered.toml")
        self.assertEqual(first.run.digest, second.run.digest)
        self.assertEqual(first.keys(), second.keys())

    def test_each_declared_choice_changes_the_run_key(self):
        base = resolve(self.root, self.repository.config("dev"))
        deeper = resolve(self.root, self.repository.config("dev", hits=1000, name="deeper"))
        other_split = resolve(self.root, self.repository.config("regression"))
        self.assertEqual(len({base.run, deeper.run, other_split.run}), 3)
        self.assertNotEqual(base.frozen_digest, deeper.frozen_digest)
        # Only the query set separates a development run from its test report.
        self.assertEqual(base.frozen_digest, resolve(self.root, self.repository.config("test")).frozen_digest)

    def test_a_manifest_change_is_an_error_until_resealed_and_then_another_run(self):
        relative = self.repository.config("dev")
        base = resolve(self.root, relative)
        path = self.root / "manifests/transform.json"
        document = json.loads(path.read_bytes())
        document["body"]["chunking"] = {"policy": "window", "tokens": 256}
        write(self.root, "manifests/transform.json", json.dumps(document))
        with self.assertRaisesRegex(ConfigError, "does not match its digest"):
            resolve(self.root, relative)
        write(self.root, "manifests/transform.json", manifest("transform", document["body"]))
        changed = resolve(self.root, relative)
        self.assertNotEqual(changed.transform, base.transform)
        self.assertNotEqual(changed.generation, base.generation)
        self.assertNotEqual(changed.run, base.run)

    def test_unknown_missing_unfilled_and_inconsistent_values_are_rejected(self):
        good = CONFIG.format(commit=self.repository.code, split="dev", hits=100)
        cases = {
            "unknown section": good + "\n[extra]\nvalue = 1\n",
            "unknown field": good.replace('engine = "in-process"', 'engine = "in-process"\nshards = 2'),
            "missing section": good.replace("[runtime]", "[load]").replace(
                'manifest = "manifests/runtime.json"',
                'arrival = "poisson"\nrates_qps = [10]\nmeasured_per_rate = 5000\nrepeats = 3'),
            "wrong type": good.replace("target_hits = 100", 'target_hits = "100"'),
            "placeholder": good.replace(self.repository.code, "<sha>"),
            "placeholder in a free-form value": good.replace('schema_revision = "none"', 'schema_revision = "<sha>"'),
            "short commit": good.replace(self.repository.code, self.repository.code[:7]),
            "run id": good.replace("fixture.bm25", "fixture bm25"),
            "split mismatch": good.replace('split = "dev"', 'split = "test"'),
            "unresolved manifest": good.replace("manifests/corpus.json", "manifests/absent.json"),
        }
        for name, text in cases.items():
            write(self.root, "config/runs/case.toml", text)
            with self.subTest(case=name), self.assertRaises(ConfigError):
                resolve(self.root, "config/runs/case.toml")

    def test_the_documented_space_and_load_sections_parse(self):
        text = CONFIG.format(commit=self.repository.code, split="dev", hits=100) + f"""
[space.gemma]
encoder = "google/embeddinggemma-300m"
revision = "r"
files = {{ "model.safetensors" = "{A}" }}
query_template = "task: search result | query: {{text}}"
doc_template = "title: none | text: {{text}}"
pooling = "model-default"
normalize = true
max_tokens = 512
dtype = "float32"
dim = 768
legacy_id = "c353e1b1"

[load]
arrival = "poisson"
rates_qps = [10, 20, 40]
measured_per_rate = 5000
repeats = 3
"""
        write(self.root, "config/runs/dense.toml", text)
        dense = resolve(self.root, "config/runs/dense.toml")
        self.assertEqual(dense.generation.spaces, (dense.spaces["gemma"].digest,))
        truncated = text.replace("dim = 768", "dim = 768\nmrl_dim = 256")
        self.assertNotEqual(parse_config(truncated.encode()), dense.config)
        write(self.root, "config/runs/mrl.toml", truncated)
        self.assertNotEqual(resolve(self.root, "config/runs/mrl.toml").run, dense.run)
        # The legacy identifier is an annotation, not part of the space.
        write(self.root, "config/runs/relabelled.toml", text.replace("c353e1b1", "other"))
        self.assertEqual(resolve(self.root, "config/runs/relabelled.toml").run, dense.run)


class ProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository(self)
        self.root = self.repository.root

    def committed(self, split, **options):
        relative = self.repository.config(split, **options)
        self.repository.commit(f"declare {split}")
        return resolve(self.root, relative)

    def test_declarations_after_the_code_commit_are_accepted(self):
        verify_provenance(self.root, self.committed("dev"))

    def test_a_directory_inside_another_repository_is_rejected(self):
        nested = self.root / "nested"
        nested.mkdir()
        with self.assertRaisesRegex(ProvenanceError, "not the top level"):
            provenance.verify_repository(nested)

    def test_uncommitted_and_untracked_files_are_rejected(self):
        resolved = self.committed("dev")
        write(self.root, "notes.txt", "untracked")
        with self.assertRaisesRegex(ProvenanceError, "uncommitted or untracked"):
            verify_provenance(self.root, resolved)
        (self.root / "notes.txt").unlink()
        write(self.root, "src/code.py", "x = 2\n")
        with self.assertRaisesRegex(ProvenanceError, "uncommitted or untracked"):
            verify_provenance(self.root, resolved)

    def test_code_committed_after_the_code_commit_is_rejected(self):
        resolved = self.committed("dev")
        write(self.root, "src/code.py", "x = 2\n")
        self.repository.commit("change code")
        with self.assertRaisesRegex(ProvenanceError, "Code changed after code_commit"):
            verify_provenance(self.root, resolved)

    def test_a_commit_that_is_unknown_or_not_an_ancestor_is_rejected(self):
        with self.assertRaises(ProvenanceError):
            verify_provenance(self.root, self.committed("dev", commit="f" * 40))
        git(self.root, "checkout", "-q", "-b", "side", self.repository.code)
        write(self.root, "src/side.py", "y = 1\n")
        side = self.repository.commit("side")
        git(self.root, "checkout", "-q", "main")
        with self.assertRaisesRegex(ProvenanceError, "not an ancestor"):
            verify_provenance(self.root, self.committed("dev", commit=side, name="side"))

    def test_run_never_accepts_the_test_split(self):
        test = self.committed("test")
        with self.assertRaisesRegex(ProvenanceError, "Only the report command"):
            authorize_split(self.root, "run", test, None)
        authorize_split(self.root, "run", self.committed("dev"), None)
        authorize_split(self.root, "run", self.committed("regression"), None)

    def test_report_requires_a_development_configuration_frozen_in_an_earlier_commit(self):
        self.repository.config("dev")
        self.repository.config("test")
        self.repository.commit("declare dev and test together")
        dev = resolve(self.root, "config/runs/dev.toml")
        test = resolve(self.root, "config/runs/test.toml")
        with self.assertRaisesRegex(ProvenanceError, "before HEAD"):
            authorize_split(self.root, "report", test, dev)
        with self.assertRaisesRegex(ProvenanceError, "requires the frozen development"):
            authorize_split(self.root, "report", test, None)
        with self.assertRaisesRegex(ProvenanceError, "requires the frozen development"):
            authorize_split(self.root, "report", test, test)
        with self.assertRaisesRegex(ProvenanceError, "reserved for the test split"):
            authorize_split(self.root, "report", dev, dev)
        write(self.root, "manifests/note.json", "{}")
        self.repository.commit("a later declaration")
        authorize_split(self.root, "report", test, dev)

    def test_report_rejects_a_configuration_tuned_after_the_freeze(self):
        dev = self.committed("dev")
        tuned = self.committed("test", hits=1000)
        with self.assertRaisesRegex(ProvenanceError, "differs from the frozen"):
            authorize_split(self.root, "report", tuned, dev)

    def test_report_rejects_a_development_configuration_edited_in_head(self):
        self.committed("dev")
        self.committed("test")
        relative = self.repository.config("dev")
        write(self.root, relative, (self.root / relative).read_text() + "\n# retuned\n")
        self.repository.commit("touch the frozen configuration")
        with self.assertRaisesRegex(ProvenanceError, "before HEAD"):
            authorize_split(self.root, "report", resolve(self.root, "config/runs/test.toml"),
                            resolve(self.root, relative))


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.repository = Repository(self)
        self.root = self.repository.root
        self.runtime = json.loads((self.root / "manifests/runtime.json").read_bytes())["body"]
        for name, value in (("python_version", "3.12.10"),):
            patcher = mock.patch.object(provenance.platform, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(provenance.sysconfig, "get_platform", return_value="win-amd64")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_matching_runtime_reports_the_locked_packages(self):
        with mock.patch.object(provenance, "version", return_value="1.0"):
            self.assertEqual(observed_runtime(self.root, self.runtime), {
                "python": "3.12.10", "platform": "win_amd64", "packages": {"example": "1.0"},
            })

    def test_interpreter_lock_and_installed_version_must_match(self):
        with mock.patch.object(provenance, "version", return_value="1.1"):
            with self.assertRaisesRegex(ProvenanceError, "example is 1.1, locked 1.0"):
                observed_runtime(self.root, self.runtime)
        with mock.patch.object(provenance, "version", side_effect=provenance.PackageNotFoundError("example")):
            with self.assertRaisesRegex(ProvenanceError, "not installed"):
                observed_runtime(self.root, self.runtime)
        with mock.patch.object(provenance, "version", return_value="1.0"):
            with self.assertRaisesRegex(ProvenanceError, "declared 3.11.0"):
                observed_runtime(self.root, {**self.runtime, "python": "3.11.0"})
            write(self.root, "locks/example.lock", LOCK + b"other==2.0\n")
            with self.assertRaisesRegex(ProvenanceError, "Lock file changed"):
                observed_runtime(self.root, self.runtime)


class OutputTests(unittest.TestCase):
    RANKINGS = {"q2": [("d1", 1e-05)], "q1": [("d2", 2.5), ("d1", 0.1)]}

    def test_trec_run_matches_an_external_hash_and_round_trips(self):
        data = trec_run(self.RANKINGS, "tag")
        # printf 'q1 Q0 d2 1 2.5 tag\nq1 Q0 d1 2 0.1 tag\nq2 Q0 d1 1 1e-05 tag\n' | sha256sum
        self.assertEqual(sha256(data).hexdigest(),
                         "176cd6001cc541f0cd73216dc5299cea0e7e1cf48efaf0bff3477b0e6a174cff")
        self.assertEqual(parse_trec_run(data), {"q1": self.RANKINGS["q1"], "q2": self.RANKINGS["q2"]})

    def test_invalid_rankings_are_rejected(self):
        for rankings in ({"q 1": [("d1", 1.0)]}, {"q1": [("d1", 1.0), ("d1", 0.5)]},
                         {"q1": [("d1", float("nan"))]}, {"q1": [("", 1.0)]}, {"q1": [("d1", 1)]}):
            with self.subTest(rankings=rankings), self.assertRaises(ValueError):
                trec_run(rankings, "tag")

    def stream(self, resolved):
        return [(qid, self.RANKINGS[qid], {"qid": qid, "metrics": {"nDCG@10": 0.5}}) for qid in sorted(self.RANKINGS)]

    def test_two_writes_of_one_run_are_byte_identical_and_timings_are_not_hashed(self):
        repository = Repository(self)
        resolved = resolve(repository.root, repository.config("dev"))
        outputs = []
        for number, elapsed in enumerate((1.0, 99.0)):
            output = repository.root / f"out-{number}"
            manifest = write_run(output, resolved, "run", iter(self.stream(resolved)), {"python": "3.12.10"},
                                 lambda: ({"nDCG@10": 0.5}, {"rank_ms": elapsed}))
            outputs.append({path.name: path.read_bytes() for path in output.iterdir()})
        self.assertEqual(set(outputs[0]), {"run.trec", "per-query.jsonl", "manifest.json", "timings.json"})
        for name in ("run.trec", "per-query.jsonl", "manifest.json"):
            self.assertEqual(outputs[0][name], outputs[1][name])
        self.assertNotEqual(outputs[0]["timings.json"], outputs[1]["timings.json"])
        self.assertEqual(outputs[0]["run.trec"], trec_run(self.RANKINGS, "fixture.bm25"))
        self.assertEqual(manifest["outputs"]["run.trec"], sha256(outputs[0]["run.trec"]).hexdigest())
        self.assertEqual(manifest["queries"], 2)
        self.assertEqual(verify_outputs(repository.root / "out-0")["keys"]["run"], resolved.run.digest)
        with self.assertRaises(FileExistsError):
            write_run(repository.root / "out-0", resolved, "run", iter(()), {}, lambda: ({}, {}))
        (repository.root / "out-1/run.trec").write_bytes(b"q1 Q0 d1 1 9.0 tag\n")
        with self.assertRaisesRegex(ValueError, "does not match the run manifest"):
            verify_outputs(repository.root / "out-1")

    def test_a_failed_or_unordered_stream_leaves_no_output(self):
        repository = Repository(self)
        resolved = resolve(repository.root, repository.config("dev"))
        output = repository.root / "out"

        def failing():
            yield from self.stream(resolved)[:1]
            raise RuntimeError("ranker stopped")

        for stream, error in ((failing(), RuntimeError), (reversed(self.stream(resolved)), ValueError),
                              (iter(self.stream(resolved) + self.stream(resolved)[-1:]), ValueError)):
            with self.subTest(error=error), self.assertRaises(error):
                write_run(output, resolved, "run", stream, {}, lambda: ({}, {}))
            self.assertEqual([path.name for path in repository.root.glob("out*")], [])

    def test_trec_reader_requires_contiguous_queries_and_ranks_from_one(self):
        lines = ["q1 Q0 d2 1 2.5 t", "q1 Q0 d1 2 0.1 t", "q2 Q0 d1 1 1.0 t"]
        self.assertEqual(list(read_trec_run(lines)), [("q1", [("d2", 2.5), ("d1", 0.1)]), ("q2", [("d1", 1.0)])])
        for broken in (lines + ["q1 Q0 d3 3 0.0 t"], ["q1 Q0 d2 2 2.5 t"], ["q1 Q1 d2 1 2.5 t"], ["q1 Q0 d2 1 2.5"],
                       ["q1 Q0 d2 1 2.5 t", "q1 Q0 d1 3 0.1 t"]):
            with self.subTest(broken=broken), self.assertRaises(ValueError):
                list(read_trec_run(broken))


class TextModeTests(unittest.TestCase):
    def test_utf8_mode_continues_and_legacy_mode_is_refused_or_rerun(self):
        with (mock.patch.object(textmode, "utf8_mode", return_value=True),
              mock.patch.object(textmode.subprocess, "call") as call):
            self.assertIsNone(textmode.rerun_in_utf8_mode())
            textmode.require_utf8_mode("reader")
            call.assert_not_called()
        with (mock.patch.object(textmode, "utf8_mode", return_value=False),
              mock.patch.object(textmode.subprocess, "call", return_value=3) as call,
              mock.patch.object(textmode.sys, "argv", ["program.py", "run", "config.toml"])):
            self.assertEqual(textmode.rerun_in_utf8_mode(), 3)
            call.assert_called_once_with([textmode.sys.executable, "-X", "utf8", "program.py", "run", "config.toml"])
            with self.assertRaisesRegex(RuntimeError, "UTF-8 mode"):
                textmode.require_utf8_mode("reader")

    def test_the_legacy_code_page_changes_non_ascii_text_silently(self):
        # The defect this guards against: UTF-8 bytes of "café" read as cp1252 give other characters.
        self.assertEqual("café".encode("utf-8").decode("cp1252"), "cafÃ©")
