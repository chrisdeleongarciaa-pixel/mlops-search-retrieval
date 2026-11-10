"""Transform an operator-exported durable PDF revision into staged artifacts."""

import argparse
from dataclasses import asdict
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import platform
from tempfile import TemporaryDirectory
import time

from search_retrieval.adapters.grobid import Grobid, IMAGE, OPTIONS
from search_retrieval.adapters.embeddinggemma import format_inputs
from search_retrieval.contracts.identity import DocumentKey, RevisionKey
from search_retrieval.ingestion.transformation import Recipe, canonical_json, transform_revision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("receipt", type=Path, help="Operator-exported revision identity and original_sha256")
    parser.add_argument("tokenizer", type=Path, help="Local verified encoder snapshot")
    parser.add_argument("output", type=Path)
    parser.add_argument("--encoder", choices=("e5", "gemma"), default="gemma")
    parser.add_argument("--generation", required=True)
    parser.add_argument("--grobid", default="http://127.0.0.1:18070")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output directory already exists")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    revision = RevisionKey(DocumentKey(**receipt["document"]), receipt["sequence"])
    root = Path(__file__).resolve().parents[1]
    parser_profile = {"image": IMAGE, "options": OPTIONS,
                      "compose_sha256": sha256((root / "infra/grobid/compose.yaml").read_bytes()).hexdigest(),
                      "config_sha256": sha256((root / "infra/grobid/grobid.yaml").read_bytes()).hexdigest()}
    parser_identity = sha256(canonical_json(parser_profile)).hexdigest()
    started = time.monotonic()
    with TemporaryDirectory(prefix=".pdf-stage-", dir=args.output.parent) as temp:
        stage = Path(temp)
        inputs = {"schema_version": 1, "revision": asdict(revision), "generation": args.generation,
                  "expected_original_sha256": receipt["original_sha256"], "parser_profile": parser_profile,
                  "parser_options": OPTIONS, "python": platform.python_version(), "platform": platform.platform(),
                  "encoder": args.encoder, "receipt_sha256": sha256(args.receipt.read_bytes()).hexdigest(),
                  "code_sha256": {name: sha256((root / name).read_bytes()).hexdigest() for name in (
                      "evaluation/pdf_transform.py", "src/search_retrieval/adapters/grobid.py",
                      "src/search_retrieval/ingestion/transformation.py")}}
        (stage / "inputs.json").write_bytes(canonical_json(inputs))
        try:
            from transformers import AutoTokenizer
            if args.encoder == "gemma":
                from search_retrieval.adapters.embeddinggemma import EmbeddingGemmaSpace
                config = json.loads((Path(__file__).parents[1] / "config/embeddinggemma-300m.json").read_text())
                space = EmbeddingGemmaSpace(config["revision"], config["git_blobs"], config["lfs_sha256"])
                root, _ = space.verify(args.tokenizer)
                format_text = lambda text: format_inputs([("", text)], "passage")[0]
            else:
                from search_retrieval.adapters.e5 import E5Space
                config = json.loads((Path(__file__).parents[1] / "config/model-sources.json").read_text())["e5"]
                space = E5Space(config["revision"], {name: spec["sha256"] for name, spec in config["files"].items()})
                root = args.tokenizer
                for name, checksum in space.files.items():
                    with (root / name).open("rb") as source:
                        if file_digest(source, "sha256").hexdigest() != checksum:
                            raise ValueError("E5 artifact checksum mismatch")
                format_text = lambda text: "passage: " + text
            tokenizer = AutoTokenizer.from_pretrained(str(root), local_files_only=True, trust_remote_code=False)
            def count(text):
                return len(tokenizer(format_text(text), add_special_tokens=True, truncation=False)["input_ids"])
            with args.pdf.open("rb") as source:
                pdf = source.read(20 * 1024 * 1024 + 1)
            tei, pages = Grobid(args.grobid).extract(pdf, receipt["original_sha256"])
            recipe = Recipe(parser_identity, space.id, args.generation)
            text, manifest = transform_revision(revision, receipt["original_sha256"], tei, recipe, count)
            if any(box["page"] > pages for fragment in manifest["fragments"] for box in fragment["boxes"]):
                raise ValueError("Parser locator exceeds original page count")
            (stage / "extraction.tei.xml").write_bytes(tei)
            (stage / "canonical.txt").write_bytes(text)
            (stage / "manifest.json").write_bytes(canonical_json(manifest))
            result = {"status": "complete", "pages": pages, "fragments": len(manifest["fragments"])}
        except Exception as error:
            result = {"status": "rejected", "error": getattr(error, "code", type(error).__name__)}
        result["elapsed_seconds"] = time.monotonic() - started
        (stage / "result.json").write_bytes(canonical_json(result))
        os.rename(stage, args.output)
    print(json.dumps(result))
    return int(result["status"] != "complete")


if __name__ == "__main__":
    raise SystemExit(main())
