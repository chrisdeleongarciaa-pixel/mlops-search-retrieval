"""Materialize SciFact vectors with an authorized, offline EmbeddingGemma snapshot."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import platform

from search_retrieval.adapters.embeddinggemma import (
    MAX_TOKENS, EmbeddingGemmaEncoder, EmbeddingGemmaSpace, format_inputs,
)
from scifact_bm25 import DATASET_SHA256, digest
from scifact_embeddings import ROOT, encode_items, load_split, write_json


SOURCE = json.loads((ROOT / "config/embeddinggemma-300m.json").read_bytes())
SPACE = EmbeddingGemmaSpace(SOURCE["revision"], SOURCE["git_blobs"], SOURCE["lfs_sha256"])
PROTOCOL = ROOT / "config/scifact-stability-v1.json"


def encoder(directory, device, threads):
    import torch

    torch.set_num_threads(threads)
    torch.set_float32_matmul_precision("highest")
    if torch.device(device).type == "cuda":
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
    return EmbeddingGemmaEncoder(directory, SPACE, device=device)


def conformance(adapter):
    torch = adapter.torch
    query = ["DNA repair", "How does a kinase affect the regulation of gene expression?"]
    passages = [("", "RNA is involved in expression."), ("Cell cycle", "cell division " * 600)]
    checks = {}
    for role, items in (("query", query), ("passage", passages)):
        observed = torch.tensor(adapter.encode(items, role=role), dtype=torch.float64)
        if role == "query":
            reference = adapter.model.encode_query(query, normalize_embeddings=True, convert_to_tensor=True)
            inputs = [f"task: search result | query: {text}" for text in query]
        else:
            inputs = [f"title: {title or 'none'} | text: {text}" for title, text in passages]
            reference = torch.stack([
                adapter.model.encode_document(text, prompt=f"title: {title or 'none'} | text: ",
                                              normalize_embeddings=True, convert_to_tensor=True)
                for title, text in passages
            ])
        reference = reference.detach().cpu().double()
        original = adapter.tokenizer(inputs, verbose=False)["input_ids"]
        tokens = adapter.tokenizer(inputs, max_length=MAX_TOKENS, truncation=True, padding=True, return_tensors="pt")
        actual_tokens = adapter.model.preprocess(format_inputs(items, role))
        same_tokens = {key: torch.equal(tokens[key], actual_tokens[key]) for key in ("input_ids", "attention_mask")}
        checks[role] = {
            "matching_tokens": same_tokens, "before": [len(ids) for ids in original],
            "after": tokens["attention_mask"].sum(1).tolist(),
            "max_absolute_error": (observed - reference).abs().max().item(),
            "min_cosine": torch.nn.functional.cosine_similarity(observed, reference).clamp(-1, 1).min().item(),
        }
    passed = all(all(row["matching_tokens"].values()) and row["max_absolute_error"] <= 1e-5
                 and row["min_cosine"] >= .99999 for row in checks.values())
    passed = passed and checks["passage"]["before"][-1] > checks["passage"]["after"][-1] == MAX_TOKENS
    return {"reference": "SentenceTransformer.encode_query / encode_document", "passed": passed, "checks": checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for field in ("model", "dataset", "output"):
        parser.add_argument(field, type=Path)
    parser.add_argument("--split", choices=("train", "test"), default="train")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if min(args.threads, args.batch_size) < 1:
        parser.error("Threads and batch size must be positive")
    adapter = encoder(args.model, args.device, args.threads)
    args.output.mkdir()
    result = conformance(adapter)
    write_json(args.output / "conformance.json", result)
    if not result["passed"]:
        raise ValueError("EmbeddingGemma conformance failed")
    env = {
        "model": SOURCE["repo"], "revision": SOURCE["revision"], "space_id": SPACE.id,
        "dataset_sha256": DATASET_SHA256, "files": adapter.checksums, "protocol_sha256": digest(PROTOCOL),
        "inference_precision": "FP32", "stored_dtype": "float32", "max_tokens": MAX_TOKENS,
        "device": str(adapter.device), "cuda_runtime": adapter.torch.version.cuda,
        "device_name": adapter.torch.cuda.get_device_name(adapter.device) if adapter.device.type == "cuda"
        else platform.processor(),
        "platform": platform.platform(), "python": platform.python_version(),
        "packages": {name: version(name) for name in
                     ("torch", "transformers", "sentence-transformers", "tokenizers", "numpy", "safetensors")},
        "batch_size": args.batch_size, "threads": args.threads,
        "document_transform": "official title and text retrieval prompt over the same SciFact title and abstract",
    }
    write_json(args.output / "environment.json", env)
    corpus, queries, _ = load_split(args.dataset, args.split, structured=True)
    batches = [("corpus", corpus, "passage")] if args.split == "train" else []
    batches.append((args.split, queries, "query"))
    for name, items, role in batches:
        info = encode_items(adapter, items, role, args.batch_size, args.output / f"{name}.npy",
                            format_texts=format_inputs)
        info.update({"space_id": SPACE.id, "protocol_sha256": digest(PROTOCOL)})
        write_json(args.output / f"{name}.json", info)


if __name__ == "__main__":
    main()
