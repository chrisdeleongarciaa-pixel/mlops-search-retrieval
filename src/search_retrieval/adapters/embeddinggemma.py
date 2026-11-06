"""Pinned EmbeddingGemma 300M with the published Sentence Transformers retrieval pipeline."""

from dataclasses import dataclass
from hashlib import file_digest, sha1, sha256
import json
from pathlib import Path


QUERY_PREFIX = "task: search result | query: "
MAX_TOKENS = 512
DIMENSIONS = 768


@dataclass(frozen=True, slots=True)
class EmbeddingGemmaSpace:
    revision: str
    git_blobs: dict[str, str]
    lfs_sha256: dict[str, str]

    @property
    def id(self):
        description = {
            "model": "google/embeddinggemma-300m", "revision": self.revision,
            "git_blobs": self.git_blobs, "lfs_sha256": self.lfs_sha256,
            "query_prefix": QUERY_PREFIX, "document": "title: {title or none} | text: {abstract}",
            "max_tokens": MAX_TOKENS, "pooling": "published-pooling-and-two-dense-projections",
            "normalization": "l2", "dimensions": DIMENSIONS, "stored_dtype": "float32", "metric": "cosine",
        }
        return sha256(json.dumps(description, sort_keys=True).encode()).hexdigest()

    def verify(self, directory: Path):
        root = directory.resolve(strict=True)
        expected = self.git_blobs.keys() | self.lfs_sha256.keys()
        optional = {".gitattributes", "README.md", "notebook.ipynb"}
        actual = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
        if not expected <= actual or actual - expected - optional:
            raise ValueError("Use the clean, complete EmbeddingGemma snapshot for the pinned revision")
        observed = {}
        for name, checksum in self.git_blobs.items():
            content = (root / name).read_bytes()
            header = f"blob {len(content)}\0".encode()
            if sha1(header + content, usedforsecurity=False).hexdigest() != checksum:
                raise ValueError(f"EmbeddingGemma Git blob mismatch: {name}")
            observed[name] = sha256(content).hexdigest()
        for name, checksum in self.lfs_sha256.items():
            with (root / name).open("rb") as source:
                actual_sha = file_digest(source, "sha256").hexdigest()
            if actual_sha != checksum:
                raise ValueError(f"EmbeddingGemma weight or tokenizer checksum mismatch: {name}")
            observed[name] = actual_sha
        return root, observed


def format_inputs(items, role):
    if role == "query":
        return [QUERY_PREFIX + text for text in items]
    if role == "passage":
        return [f"title: {title or 'none'} | text: {abstract}" for title, abstract in items]
    raise ValueError("EmbeddingGemma requires an explicit retrieval role")


class EmbeddingGemmaEncoder:
    def __init__(self, directory, space, *, device="cpu"):
        import torch
        from sentence_transformers import SentenceTransformer

        root, self.checksums = space.verify(directory)
        self.space, self.torch, self.device = space, torch, torch.device(device)
        self.model = SentenceTransformer(
            str(root), device=device, trust_remote_code=False, local_files_only=True,
            model_kwargs={"dtype": torch.float32, "use_safetensors": True},
        ).float().eval()
        self.model.max_seq_length = MAX_TOKENS
        self.tokenizer = self.model.tokenizer

    def forward_tokens(self, inputs):
        vectors = self.model(dict(inputs))["sentence_embedding"].float()
        return self.torch.nn.functional.normalize(vectors, dim=1)

    def encode(self, items, *, role):
        if not items:
            return []
        tokens = self.model.tokenize(format_inputs(items, role))
        tokens = {name: tensor.to(self.device) for name, tensor in tokens.items()}
        with self.torch.inference_mode(), self.torch.autocast(self.device.type, enabled=False):
            vectors = self.forward_tokens(tokens)
        if (vectors.shape != (len(items), DIMENSIONS) or not self.torch.isfinite(vectors).all()
                or not ((vectors.norm(dim=1) - 1).abs() <= 1e-5).all()):
            raise ValueError("EmbeddingGemma produced an incompatible vector")
        return vectors.cpu().tolist()
