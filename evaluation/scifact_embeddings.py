"""Materialize the pinned E5 and build independently verifiable SciFact inputs."""

import argparse
from csv import DictReader
from importlib.metadata import version
from io import StringIO
import json
from pathlib import Path
import platform
import shutil
from urllib.request import urlopen
from zipfile import ZipFile

from search_retrieval.adapters.e5 import E5Encoder, E5Space

from scifact_bm25 import DATASET_SHA256, digest


ROOT = Path(__file__).resolve().parents[1]
DATASET_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
MODEL = json.loads((ROOT / "config/model-sources.json").read_text(encoding="utf-8"))["e5"]
SPACE = E5Space(MODEL["revision"], {name: spec["sha256"] for name, spec in MODEL["files"].items()})


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")


def download(url, path, checksum, size=None):
    if path.is_file() and digest(path) == checksum:
        return
    if size is not None and shutil.disk_usage(path.parent).free < size:
        raise OSError(f"Insufficient disk for {path.name}")
    temporary = path.with_suffix(path.suffix + ".part")
    with urlopen(url, timeout=90) as source, temporary.open("wb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    if (size is not None and temporary.stat().st_size != size) or digest(temporary) != checksum:
        raise ValueError(f"Download checksum or size mismatch: {path.name}")
    temporary.replace(path)


def materialize(model, dataset):
    model.mkdir(parents=True, exist_ok=True)
    for name, spec in MODEL["files"].items():
        base = f"https://huggingface.co/{MODEL['repo']}/resolve/{MODEL['revision']}"
        download(f"{base}/{name}?download=true", model / name, spec["sha256"], spec["bytes"])
    download(DATASET_URL, dataset, DATASET_SHA256)


def load_split(dataset, split):
    if digest(dataset) != DATASET_SHA256:
        raise ValueError("SciFact archive checksum mismatch")
    with ZipFile(dataset) as source:
        corpus = {
            doc["_id"]: doc.get("title", "") + " " + doc["text"]
            for doc in map(json.loads, source.read("scifact/corpus.jsonl").splitlines())
        }
        queries = {
            query["_id"]: query["text"]
            for query in map(json.loads, source.read("scifact/queries.jsonl").splitlines())
        }
        labels = {}
        qrels = source.read(f"scifact/qrels/{split}.tsv").decode()
        for row in DictReader(StringIO(qrels), delimiter="\t"):
            labels.setdefault(row["query-id"], {})[row["corpus-id"]] = int(row["score"])
    if (len(corpus) != 5183 or len(labels) != {"train": 809, "test": 300}[split]
            or not labels.keys() <= queries.keys() or any(not docs.keys() <= corpus.keys() for docs in labels.values())):
        raise ValueError("Incomplete SciFact snapshot")
    return corpus, {qid: queries[qid] for qid in sorted(labels)}, labels


def encoder(model, device, threads):
    import torch

    torch.set_num_threads(threads)
    torch.set_float32_matmul_precision("highest")
    if device.startswith("cuda"):
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
    return E5Encoder(model, SPACE, device=device)


def conformance(adapter, model):
    import torch
    from transformers import AutoModel, AutoTokenizer

    observed = {}

    def capture(_module, _args, inputs):
        for key in ("input_ids", "attention_mask"):
            observed[key] = inputs[key].detach().cpu()

    handle = adapter.model.register_forward_pre_hook(capture, with_kwargs=True)
    try:
        vectors = torch.tensor(adapter.encode_batch([
            ("query", "RNA"), ("passage", "Una revisión sobre salud pública."),
            ("query", "How does inhibition affect DNA repair?"), ("passage", "cell " * 700),
        ]), dtype=torch.float32)
    finally:
        handle.remove()

    # Independent implementation of the model card's Transformers example.
    tokenizer = AutoTokenizer.from_pretrained(model, local_files_only=True, trust_remote_code=False)
    reference = AutoModel.from_pretrained(
        model, local_files_only=True, trust_remote_code=False, use_safetensors=True, dtype=torch.float32,
    ).to(adapter.device).eval()
    texts = [
        "query: RNA", "passage: Una revisión sobre salud pública.",
        "query: How does inhibition affect DNA repair?", "passage: " + "cell " * 700,
    ]
    inputs = tokenizer(texts, max_length=512, padding=True, truncation=True, return_tensors="pt")
    matches = {key: torch.equal(value, inputs[key]) for key, value in observed.items()}
    with torch.inference_mode():
        inputs = inputs.to(adapter.device)
        hidden = reference(**inputs).last_hidden_state
        mask = inputs["attention_mask"]
        hidden = hidden.masked_fill(~mask[..., None].bool(), 0.0)
        expected = torch.nn.functional.normalize(hidden.sum(dim=1) / mask.sum(dim=1)[..., None], p=2, dim=1).cpu()
    errors = (vectors - expected).abs().amax(dim=1).tolist()
    cosines = torch.nn.functional.cosine_similarity(vectors.double(), expected.double()).clamp(-1, 1).tolist()
    original = [len(ids) for ids in tokenizer(texts, verbose=False)["input_ids"]]
    effective = inputs["attention_mask"].sum(dim=1).tolist()
    return {
        "token_matches": matches, "tokens_before": original, "tokens_after": effective,
        "max_absolute_error": errors, "cosine": cosines,
        "limits": {"max_absolute_error": 1e-5, "min_cosine": .99999},
        "passed": len(matches) == 2 and all(matches.values()) and max(errors) <= 1e-5
        and min(cosines) >= .99999 and original[-1] > effective[-1] == 512 and len(set(effective)) > 1,
    }


def encode_items(adapter, items, role, batch_size, destination):
    import numpy as np

    ids = sorted(items)
    lengths = {}
    for start in range(0, len(ids), 128):
        batch = ids[start:start + 128]
        texts = [f"{role}: {items[item]}" for item in batch]
        raw = adapter.tokenizer(texts, verbose=False)["input_ids"]
        truncated = adapter.tokenizer(texts, max_length=512, truncation=True)["input_ids"]
        lengths.update((item, (len(before), len(after)))
                       for item, before, after in zip(batch, raw, truncated, strict=True))
    # Group similar lengths for padding efficiency, storing rows in ID order.
    order = sorted(range(len(ids)), key=lambda idx: lengths[ids[idx]][1])
    vectors = np.empty((len(ids), 768), dtype="float32")
    for start in range(0, len(ids), batch_size):
        batch = order[start:start + batch_size]
        vectors[batch] = adapter.encode([items[ids[idx]] for idx in batch], role=role)
        if start % (batch_size * 8) == 0 or start + len(batch) == len(ids):
            print(f"{role}: {start + len(batch)}/{len(ids)}", flush=True)
    np.save(destination, vectors, allow_pickle=False)
    return {
        "ids": ids, "vectors_sha256": digest(destination), "tokens": {
            item: {"before": before, "after": after} for item, (before, after) in sorted(lengths.items())
        },
        "truncated_ids": sorted(item for item, (before, after) in lengths.items() if before > after),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", type=Path)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--batch-size", type=int, default=16)
    args = parser.parse_args()
    if args.threads < 1 or args.batch_size < 1:
        parser.error("Thread and batch sizes must be positive")
    if args.download:
        materialize(args.model, args.dataset)
    corpus, train, _ = load_split(args.dataset, "train")
    args.output.mkdir()
    adapter = encoder(args.model, args.device, args.threads)
    result = conformance(adapter, args.model)
    write_json(args.output / "conformance.json", result)
    if not result["passed"]:
        raise ValueError("E5 conformance failed")
    manifest = {
        "dataset_sha256": DATASET_SHA256, "model": MODEL["repo"], "revision": MODEL["revision"],
        "files": SPACE.files, "space_id": SPACE.id, "inference_precision": "FP32",
        "stored_dtype": "float32", "device": str(adapter.model.device),
        "device_name": adapter.torch.cuda.get_device_name(adapter.model.device) if adapter.model.device.type == "cuda"
        else platform.processor(),
        "platform": platform.platform(), "python": platform.python_version(),
        "packages": {name: version(name) for name in ("torch", "transformers", "tokenizers", "numpy", "safetensors")},
        "threads": args.threads, "batch_size": args.batch_size,
        "document_transform": "title (empty if missing) + U+0020 + abstract", "max_tokens": 512,
        "code_sha256": {path.name: digest(path) for path in (Path(__file__), ROOT / "src/search_retrieval/adapters/e5.py")},
    }
    write_json(args.output / "environment.json", manifest)
    for name, items, role in (("corpus", corpus, "passage"), ("train", train, "query")):
        info = encode_items(adapter, items, role, args.batch_size, args.output / f"{name}.npy")
        write_json(args.output / f"{name}.json", info)


if __name__ == "__main__":
    main()
