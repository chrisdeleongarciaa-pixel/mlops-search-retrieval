"""Encode a corpus or its queries into canonical vector shards for one registered space.

    python evaluation/embed.py encode --space SPACE --corpus CORPUS --transform TRANSFORM
        [--queries QUERY_SET] --model-dir DIR --data-dir DIR --vectors-dir DIR
        [--device cuda] [--batch-size 64] [--shard-rows 50000] [--parity-sample 1000]
    python evaluation/embed.py conformance --space SPACE --model-dir DIR [--device cuda]
    python evaluation/embed.py verify VECTOR_SET
    python evaluation/embed.py derive VECTOR_SET --operation mrl:256|binary|int8 --vectors-dir DIR
    python evaluation/embed.py parity DOCUMENT_SET QUERY_SET

Manifests are repository-relative. An interrupted `encode` continues from its last complete
shard when run again with the same arguments. Derived forms never call the encoder.
"""

import argparse
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import sys
from time import perf_counter
from zipfile import ZipFile

import numpy as np

from search_retrieval.adapters.space_encoder import SpaceEncoder, render
from search_retrieval.experiment.manifests import ManifestError, read_manifest, require, seal
from search_retrieval.experiment.provenance import ProvenanceError, _git, verify_clean, verify_repository
from search_retrieval.experiment.run_config import CORPUS, QUERY_SET, TRANSFORM, ConfigError, read_space, space_key
from search_retrieval.experiment.textmode import require_utf8_mode, rerun_in_utf8_mode
from vector_store import (
    Identity, VectorSetWriter, VectorStoreError, derive, derived_space, ids_bytes,
    parity_report, verify_vector_set,
)


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_FORMS = ("title-space-text", "title-text-fields", "passage")
CONFORMANCE = {"max_absolute_error": 1e-5, "min_cosine": 0.99999}


class SyntheticEncoder:
    """Deterministic stand-in for pipeline tests. Only spaces named `synthetic/...` may use it."""

    def __init__(self, body: dict):
        if not body["encoder"].startswith("synthetic/"):
            raise ConfigError("The synthetic adapter serves only synthetic spaces")
        self.max_tokens, self.dimensions = body["max_tokens"], body["dim"]

    def token_counts(self, texts):
        return [len(text.split()) for text in texts]

    def encode_texts(self, texts):
        rows = np.empty((len(texts), self.dimensions), dtype=np.float64)
        for index, text in enumerate(texts):
            words = " ".join(text.split()[:self.max_tokens])
            seed = int.from_bytes(sha256(words.encode("utf-8")).digest()[:8], "big")
            rows[index] = np.random.default_rng(seed).standard_normal(self.dimensions)
        return (rows / np.linalg.norm(rows, axis=1, keepdims=True)).astype(np.float32)


def build_encoder(body: dict, model_dir: Path | None, device: str):
    if body.get("adapter") == "synthetic-hash":
        return SyntheticEncoder(body)
    if model_dir is None:
        raise ConfigError("--model-dir is required for this space")
    return SpaceEncoder(body, model_dir, device=device)


def document_fields(form: str, title: str, text: str) -> tuple[str, str]:
    """The title and text handed to the document template under one transform."""
    if form == "title-space-text":
        return "", title + " " + text
    if form == "title-text-fields":
        return title, text
    if form == "passage":
        return "", text
    raise ConfigError(f"Unsupported document form: {form}")


def _archive(corpus: dict, data_dir: Path) -> Path:
    path = data_dir / corpus["archive"]
    digest = sha256()
    with path.open("rb") as source:
        while block := source.read(1 << 20):
            digest.update(block)
    if digest.hexdigest() != corpus["archive_sha256"]:
        raise ManifestError("Corpus archive does not match its manifest")
    return path


def _ir_dataset(identifier: str, data_dir: Path):
    os.environ["IR_DATASETS_HOME"] = str((data_dir / "ir_datasets").resolve())
    require_utf8_mode("ir_datasets")
    import ir_datasets

    return ir_datasets.load(identifier)


def iterate_documents(corpus: dict, transform: dict, data_dir: Path):
    """Yield `(id, title, text)` in corpus order."""
    form = transform["configuration"].get("document")
    if form not in DOCUMENT_FORMS or transform["chunking"] != {"policy": "identity"}:
        raise ConfigError("Unsupported transform for batch embedding")
    if corpus["format"] == "beir-zip":
        with ZipFile(_archive(corpus, data_dir)) as archive:
            lines = archive.read(corpus["corpus_member"]).splitlines()
        for line in lines:
            row = json.loads(line)
            yield (row["_id"], *document_fields(form, row.get("title", ""), row["text"]))
    elif corpus["format"] == "ir-datasets-docs":
        missing = [name for name in ("ir_datasets", "documents_sha256") if name not in corpus]
        if missing:
            raise ManifestError(f"corpus manifest of this format requires {missing}")
        digest = sha256()
        for row in _ir_dataset(corpus["ir_datasets"], data_dir).docs_iter():
            digest.update(f"{row.doc_id}\t{row.text}\n".encode("utf-8"))
            yield (row.doc_id, *document_fields(form, "", row.text))
        if digest.hexdigest() != corpus["documents_sha256"]:
            raise ManifestError("Documents do not match the corpus manifest")
    else:
        raise ConfigError(f"Batch embedding does not read the corpus format {corpus['format']}")


def iterate_queries(query_set: dict, corpus: dict, data_dir: Path):
    """Yield `(id, "", text)` for every query of the set, in ID order. Judgments are not read."""
    if "ir_datasets" in query_set:
        digest, rows = sha256(), {}
        for row in _ir_dataset(query_set["ir_datasets"], data_dir).queries_iter():
            digest.update(f"{row.query_id}\t{row.text}\n".encode("utf-8"))
            rows[row.query_id] = row.text
        if digest.hexdigest() != query_set["queries_sha256"]:
            raise ManifestError("Queries do not match the query-set manifest")
    elif "queries_member" in query_set and corpus["format"] == "beir-zip":
        with ZipFile(_archive(corpus, data_dir)) as archive:
            data = archive.read(query_set["queries_member"])
        if sha256(data).hexdigest() != query_set["queries_sha256"]:
            raise ManifestError("Queries do not match the query-set manifest")
        rows = {row["_id"]: row["text"] for row in map(json.loads, data.splitlines())}
    else:
        raise ConfigError("The query-set manifest names no readable query source")
    for item in sorted(rows):
        yield item, "", rows[item]


def encode_set(writer: VectorSetWriter, items, encoder, template: str, *, batch_size: int) -> dict:
    """Fill the vector set from `(id, title, text)` items, skipping rows that are already stored."""
    kept = writer.existing_ids_sha256()
    resumed, shard, stats = writer.rows, [], {"encoded": 0, "seconds": 0.0}

    def flush():
        ids = [item for item, _, _ in shard]
        if kept:
            rows, expected = kept.pop(0)
            if len(ids) != rows or sha256(ids_bytes(ids)).hexdigest() != expected:
                raise VectorStoreError("The collection changed since the existing shards were written")
            return
        texts = [render(template, title, text) for _, title, text in shard]
        counts = encoder.token_counts(texts)
        vectors = np.empty((len(texts), encoder.dimensions), dtype=np.float32)
        # Similar lengths share a batch; rows return to collection order.
        order = sorted(range(len(texts)), key=counts.__getitem__)
        started = perf_counter()
        for start in range(0, len(order), batch_size):
            batch = order[start:start + batch_size]
            vectors[batch] = encoder.encode_texts([texts[index] for index in batch])
        stats["seconds"] += perf_counter() - started
        stats["encoded"] += len(texts)
        writer.append(ids, vectors, sum(count > encoder.max_tokens for count in counts))
        print(f"{writer.identity.role}: {writer.rows} rows", flush=True)

    for item in items:
        shard.append(item)
        if len(shard) == writer.shard_rows:
            flush()
            shard = []
    if shard:
        flush()
    if kept:
        raise VectorStoreError("The collection is shorter than the existing shards")
    return {"resumed_from_rows": resumed, "encoded_rows": stats["encoded"], "encode_seconds": stats["seconds"],
            "rows_per_second": stats["encoded"] / stats["seconds"] if stats["seconds"] else None}


def observed_environment(device: str) -> dict:
    packages = {}
    for name in ("torch", "transformers", "sentence-transformers", "tokenizers", "numpy", "safetensors"):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            pass
    environment = {"python": platform.python_version(), "platform": platform.platform(), "device": device,
                   "packages": packages}
    if device.startswith("cuda"):
        import torch

        environment["device_name"] = torch.cuda.get_device_name(torch.device(device))
        environment["peak_gpu_memory_bytes"] = torch.cuda.max_memory_allocated(torch.device(device))
    return environment


def set_directory(vectors_dir: Path, space_digest: str, identity: Identity) -> Path:
    collection = sha256(json.dumps(identity.collection, sort_keys=True).encode("utf-8")).hexdigest()
    return vectors_dir / space_digest[:16] / f"{identity.role}-{collection[:16]}"


def command_encode(args) -> dict:
    verify_repository(ROOT)
    verify_clean(ROOT)
    commit = _git(ROOT, "rev-parse", "HEAD")
    body, space = read_space(ROOT, args.space)
    corpus, corpus_digest = read_manifest(ROOT, args.corpus, "corpus")
    require(corpus, "corpus", *CORPUS)
    transform, transform_digest = read_manifest(ROOT, args.transform, "transform")
    require(transform, "transform", TRANSFORM)
    if args.queries:
        query_set, _ = read_manifest(ROOT, args.queries, "query-set")
        require(query_set, "query-set", *QUERY_SET)
        identity = Identity(space, "query", {"queries": query_set["queries_sha256"]})
        items, template, expected = iterate_queries(query_set, corpus, args.data_dir), body["query_template"], None
    else:
        identity = Identity(space, "document", {"corpus": corpus_digest, "transform": transform_digest})
        items = iterate_documents(corpus, transform, args.data_dir)
        template, expected = body["doc_template"], corpus["documents"]
    directory = set_directory(args.vectors_dir, space.digest, identity)
    parity_every = None
    if space.dtype == "float16":
        parity_every = max(1, (expected or args.parity_sample) // args.parity_sample)
    writer = VectorSetWriter(directory, identity, shard_rows=args.shard_rows, parity_every=parity_every)
    encoder = build_encoder(body, args.model_dir, args.device)
    if encoder.dimensions != space.dimensions or encoder.max_tokens != space.max_tokens:
        raise ConfigError("The encoder does not implement the dimensions or token limit of the space")
    stats = encode_set(writer, items, encoder, template, batch_size=args.batch_size)
    sealed = writer.finish(expected)
    (directory.parent / "space.json").write_bytes((json.dumps(seal("space", body), indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    # Observations of this execution. They are not part of the vector-set identity.
    observation = {"code_commit": commit, "batch_size": args.batch_size, **stats, **observed_environment(args.device)}
    (directory / "encoding.json").write_bytes((json.dumps(observation, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    return {"vector_set": sealed["sha256"], "directory": str(directory), "rows": sealed["body"]["rows"],
            "truncated": sealed["body"]["truncated"], "space": space.digest, **stats}


def command_conformance(args) -> dict:
    """Compare the adapter with the reference implementation of the model on fixed inputs."""
    body, space = read_space(ROOT, args.space)
    encoder = SpaceEncoder(body, args.model_dir, device=args.device)
    torch = encoder.torch
    inputs = [("query", "", "DNA repair"), ("query", "", "How does a kinase affect the regulation of gene expression?"),
              ("document", "", "RNA is involved in expression."), ("document", "Cell cycle", "cell division " * 4000)]
    rendered = [render(body["query_template" if role == "query" else "doc_template"], title, text)
                for role, title, text in inputs]
    observed = torch.tensor(encoder.encode_texts(rendered), dtype=torch.float64)
    counts = encoder.token_counts(rendered)
    if encoder.sentence_transformer is not None:
        # Reference: SentenceTransformer.encode applying each prompt itself.
        rows = []
        for (_, _, text), full in zip(inputs, rendered, strict=True):
            if not full.endswith(text):
                raise ConfigError("Conformance needs templates that end with {text}")
            rows.append(encoder.sentence_transformer.encode(
                text, prompt=full[:len(full) - len(text)], normalize_embeddings=body["normalize"],
                convert_to_tensor=True))
        reference = torch.stack(rows).detach().cpu().double()
        name = "SentenceTransformer.encode with the prompt of each template"
    else:
        # Reference: the Transformers example of the model card, on a second copy of the weights.
        from transformers import AutoModel, AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(args.model_dir, local_files_only=True, trust_remote_code=False)
        model = AutoModel.from_pretrained(args.model_dir, local_files_only=True, trust_remote_code=False,
                                          use_safetensors=True, dtype=torch.float32).to(encoder.device).eval()
        batch = tokenizer(rendered, max_length=space.max_tokens, padding=True, truncation=True, return_tensors="pt")
        with torch.inference_mode():
            batch = batch.to(encoder.device)
            hidden = model(**batch).last_hidden_state
            mask = batch["attention_mask"]
            hidden = hidden.masked_fill(~mask[..., None].bool(), 0.0)
            reference = (hidden.sum(dim=1) / mask.sum(dim=1)[..., None]).cpu().double()
        if body["normalize"]:
            reference = torch.nn.functional.normalize(reference, p=2, dim=1)
        name = "Transformers masked mean pooling on an independent model instance"
    error = (observed - reference).abs().max().item()
    cosine = torch.nn.functional.cosine_similarity(observed, reference).clamp(-1, 1).min().item()
    truncation_exercised = counts[-1] > space.max_tokens
    return {
        "space": space.digest, "reference": name, "tokens_before_truncation": counts,
        "max_absolute_error": error, "min_cosine": cosine, "limits": CONFORMANCE,
        "truncation_exercised": truncation_exercised, **observed_environment(args.device),
        "passed": bool(error <= CONFORMANCE["max_absolute_error"] and cosine >= CONFORMANCE["min_cosine"]
                       and truncation_exercised),
    }


def parse_operation(text: str) -> dict:
    kind, _, value = text.partition(":")
    if kind == "mrl" and value.isdigit():
        return {"kind": "mrl", "dimensions": int(value)}
    if kind in ("binary", "int8") and not value:
        return {"kind": kind}
    raise ConfigError(f"Unknown operation: {text}")


def command_derive(args) -> dict:
    source = args.vector_set
    parent_body = json.loads((source.parent / "space.json").read_bytes())["body"]
    parent = space_key(parent_body)
    operation = parse_operation(args.operation)
    space = derived_space(parent, operation)
    destination = args.vectors_dir / space.digest[:16] / source.name
    sealed = derive(source, destination, parent, operation)
    derived_body = {**{name: value for name, value in parent_body.items() if name != "legacy_id"},
                    "dtype": space.dtype, "mrl_dim": space.mrl_dimensions, "quantization": space.quantization}
    (destination.parent / "space.json").write_bytes(
        (json.dumps(seal("space", derived_body), indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return {"vector_set": sealed["sha256"], "directory": str(destination), "space": space.digest,
            "derived_from": sealed["body"]["derived_from"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    encode = commands.add_parser("encode")
    for name in ("space", "corpus", "transform"):
        encode.add_argument(f"--{name}", required=True)
    encode.add_argument("--queries")
    encode.add_argument("--model-dir", type=Path)
    encode.add_argument("--data-dir", type=Path, required=True)
    encode.add_argument("--vectors-dir", type=Path, required=True)
    encode.add_argument("--device", default="cpu")
    encode.add_argument("--batch-size", type=int, default=64)
    encode.add_argument("--shard-rows", type=int, default=50_000)
    encode.add_argument("--parity-sample", type=int, default=1000)
    conformance = commands.add_parser("conformance")
    conformance.add_argument("--space", required=True)
    conformance.add_argument("--model-dir", type=Path, required=True)
    conformance.add_argument("--device", default="cpu")
    commands.add_parser("verify").add_argument("vector_set", type=Path)
    derivation = commands.add_parser("derive")
    derivation.add_argument("vector_set", type=Path)
    derivation.add_argument("--operation", required=True)
    derivation.add_argument("--vectors-dir", type=Path, required=True)
    parity = commands.add_parser("parity")
    parity.add_argument("documents", type=Path)
    parity.add_argument("queries", type=Path)
    args = parser.parse_args()
    rerun = rerun_in_utf8_mode()
    if rerun is not None:
        return rerun
    try:
        if args.command == "encode":
            if min(args.batch_size, args.shard_rows, args.parity_sample) < 1:
                raise ConfigError("Batch size, shard rows and parity sample must be positive")
            result = command_encode(args)
        elif args.command == "conformance":
            result = command_conformance(args)
        elif args.command == "verify":
            sealed = verify_vector_set(args.vector_set)
            result = {"verified": str(args.vector_set), "vector_set": sealed["sha256"], "rows": sealed["body"]["rows"]}
        elif args.command == "derive":
            result = command_derive(args)
        else:
            result = parity_report(args.documents, args.queries)
    except (ConfigError, ManifestError, ProvenanceError, VectorStoreError, ValueError, OSError, RuntimeError) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return int(args.command in ("parity", "conformance") and not result["passed"])


if __name__ == "__main__":
    raise SystemExit(main())
