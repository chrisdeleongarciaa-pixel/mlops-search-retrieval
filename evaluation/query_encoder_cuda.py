"""Benchmark eager GPU, torch.compile, and confirmed CUDA Graph replay on the same queries."""

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import platform
from time import perf_counter_ns

import numpy as np

from scifact_bm25 import digest
from scifact_dense import cosine_matrix, overlap, read_vectors, top_exact
from scifact_embeddings import encoder as e5_encoder, load_split, write_json
from scifact_stability import PROTOCOL_PATH, validate_bundle
from vespa_lexical import percentiles


BUCKETS = (32, 64, 128, 256, 512)


def elapsed(start):
    return (perf_counter_ns() - start) / 1_000_000


def bucket_for(length):
    return next(size for size in BUCKETS if length <= size)


def padded_query(adapter, text, model, fixed_size=None):
    if model == "e5":
        tokens = adapter.tokenizer(["query: " + text], max_length=512, truncation=True, return_tensors="pt")
    else:
        from search_retrieval.adapters.embeddinggemma import format_inputs
        tokens = adapter.model.preprocess(format_inputs([text], "query"))
    length = tokens["input_ids"].shape[1]
    size = fixed_size or bucket_for(length)
    if size < length:
        raise ValueError("Selected bucket cannot hold the effective query")
    # The model sees the same non-padding token IDs and attention mask in every variant.
    padding = size - length
    side = adapter.tokenizer.padding_side
    sides = (padding, 0) if side == "left" else (0, padding)
    for name in tokens.keys():
        pad_id = adapter.tokenizer.pad_token_id if name == "input_ids" else 0
        tokens[name] = adapter.torch.nn.functional.pad(tokens[name], sides, value=pad_id)
    return dict(tokens), length, size


class GraphReplay:
    """One explicitly captured graph per batch-one bucket. The runner is single-client."""

    def __init__(self, torch, forward, examples):
        self.graphs = {}
        stream = torch.cuda.Stream()
        for size, tokens in examples.items():
            static = {name: value.clone() for name, value in tokens.items()}
            stream.wait_stream(torch.cuda.current_stream())
            with torch.cuda.stream(stream):
                for _ in range(3):
                    forward(static)
            torch.cuda.current_stream().wait_stream(stream)
            graph = torch.cuda.CUDAGraph()
            with torch.cuda.graph(graph, stream=stream):
                output = forward(static)
            self.graphs[size] = (graph, static, output)

    def __call__(self, tokens, size):
        graph, static, result = self.graphs[size]
        for name, tensor in tokens.items():
            static[name].copy_(tensor)
        graph.replay()
        return result


def device_inputs(tokens, device):
    return {name: value.to(device) for name, value in tokens.items()}


def measure(adapter, queries, model, mode, sample, destination):
    torch = adapter.torch
    device = torch.device(adapter.device)
    torch.cuda.reset_peak_memory_stats(device)
    start = perf_counter_ns()
    forward = adapter.forward_tokens
    if mode != "eager":
        forward = torch.compile(forward, fullgraph=True, dynamic=False, options={"triton.cudagraphs": False})
    examples = {}
    for size in BUCKETS:
        tokens, _, _ = padded_query(adapter, sample, model, fixed_size=size)
        examples[size] = device_inputs(tokens, device)
    with torch.inference_mode(), torch.autocast("cuda", enabled=False):
        for example in examples.values():
            forward(example)
        graph = GraphReplay(torch, forward, examples) if mode == "compiled_graph" else None
        torch.cuda.synchronize(device)
        setup_ms = elapsed(start)
        baseline_allocated = torch.cuda.memory_allocated(device)
        baseline_reserved = torch.cuda.memory_reserved(device)
        setup_peak_allocated = torch.cuda.max_memory_allocated(device)
        setup_peak_reserved = torch.cuda.max_memory_reserved(device)
        torch.cuda.reset_peak_memory_stats(device)
        samples, vectors = [], []
        for query_id, text in queries.items():
            request = perf_counter_ns()
            tokens, length, size = padded_query(adapter, text, model)
            tokenize_ms = elapsed(request)
            transfer = perf_counter_ns()
            inputs = device_inputs(tokens, device)
            result = graph(inputs, size) if graph else forward(inputs)
            vector = result.cpu().numpy().copy()
            gpu_transfer_ms = elapsed(transfer)
            samples.append({
                "query_id": query_id, "tokens": length, "bucket": size, "tokenize_ms": tokenize_ms,
                "gpu_and_transfer_ms": gpu_transfer_ms, "encoder_ms": elapsed(request),
            })
            vectors.append(vector[0])
    values = np.stack(vectors)
    if (values.dtype != np.float32 or values.shape != (len(queries), 768)
            or not np.all(np.abs(np.linalg.norm(values, axis=1) - 1) <= 1e-5)):
        raise ValueError("The runtime returned incompatible vectors")
    write_json(destination / f"{mode}-samples.json", samples)
    return values, {
        "setup_ms": setup_ms, "captured_buckets": sorted(graph.graphs) if graph else [],
        "resident_allocated_bytes": baseline_allocated, "resident_reserved_bytes": baseline_reserved,
        "setup_peak_allocated_bytes": setup_peak_allocated, "setup_peak_reserved_bytes": setup_peak_reserved,
        "peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
        "peak_reserved_bytes": torch.cuda.max_memory_reserved(device),
        "samples_sha256": digest(destination / f"{mode}-samples.json"),
        "timing_ms": {stage: percentiles([row[stage] for row in samples])
                      for stage in ("tokenize_ms", "gpu_and_transfer_ms", "encoder_ms")},
    }


def compare(expected, actual, ids, documents):
    reference = expected.astype(np.float64)
    measured = actual.astype(np.float64)
    error = np.abs(reference - measured).max()
    cosine = np.sum(reference * measured, axis=1) / (
        np.linalg.norm(reference, axis=1) * np.linalg.norm(measured, axis=1))
    base_scores = cosine_matrix(ids, documents, expected)
    new_scores = cosine_matrix(ids, documents, actual)
    recall = [
        overlap(top_exact(dict(zip(ids, a.tolist(), strict=True))),
                top_exact(dict(zip(ids, b.tolist(), strict=True))), 10)
        for a, b in zip(base_scores, new_scores, strict=True)
    ]
    return {"max_absolute_error": float(error), "min_cosine": min(1.0, float(cosine.min())),
            "min_top10_overlap": min(recall),
            "passed": bool(error <= 1e-5 and cosine.min() >= .99999 and min(recall) == 1)}


def main():
    import torch

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", choices=("e5", "gemma"))
    for name in ("snapshot", "dataset", "embeddings", "output"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    if not torch.cuda.is_available():
        parser.error("CUDA is required to measure and confirm graph replay")
    if args.model == "gemma":
        from scifact_gemma import encoder
    else:
        encoder = e5_encoder
    adapter = encoder(args.snapshot, "cuda", args.threads)
    env = validate_bundle(args.embeddings)
    if env["space_id"] != adapter.space.id:
        raise ValueError("The runtime and reference index belong to different vector spaces")
    corpus, train, _ = load_split(args.dataset, "train")
    ids, documents = read_vectors(args.embeddings, "corpus", corpus)
    _, queries, _ = load_split(args.dataset, "test")
    # An official train query is used for all pre-registered capture shapes.
    warm = min(train.values(), key=lambda text: (len(text), text))
    args.output.mkdir()
    native = np.asarray([adapter.encode([text], role="query")[0] for text in queries.values()], dtype=np.float32)
    observations = {}
    report = {
        "space": adapter.space.id, "protocol_sha256": digest(PROTOCOL_PATH),
        "device": torch.cuda.get_device_name(), "capability": torch.cuda.get_device_capability(),
        "device_total_bytes": torch.cuda.get_device_properties(0).total_memory,
        "cuda_runtime": torch.version.cuda, "torch": version("torch"), "transformers": version("transformers"),
        "platform": platform.platform(), "precision": "FP32", "queries": len(queries), "batch_size": 1,
        "buckets": list(BUCKETS), "query_cache": False, "runtime": observations,
    }
    for mode in ("eager", "compiled", "compiled_graph"):
        torch.cuda.empty_cache()
        values, summary = measure(adapter, queries, args.model, mode, warm, args.output)
        if mode == "eager":
            reference = values
            summary["parity_with_unpadded"] = compare(native, values, ids, documents)
        else:
            summary["parity"] = compare(reference, values, ids, documents)
        observations[mode] = summary
        write_json(args.output / "manifest.json", report)
    print(json.dumps(report), flush=True)
    if not all(item.get("parity", item.get("parity_with_unpadded"))["passed"] for item in observations.values()):
        raise SystemExit("CUDA runtime failed vector or ranking parity")


if __name__ == "__main__":
    main()
