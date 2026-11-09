"""Offline E5 adapter. The caller provides a resolved, hash-verified model."""

from dataclasses import dataclass
from hashlib import file_digest, sha256
import json
from pathlib import Path


@dataclass(frozen=True, slots=True)
class E5Space:
    revision: str
    files: dict[str, str]

    @property
    def id(self) -> str:
        manifest = {
            "model": "intfloat/multilingual-e5-base", "revision": self.revision, "files": self.files,
            "prefixes": {"query": "query: ", "passage": "passage: "},
            "max_tokens": 512, "pooling": "masked_mean", "normalization": "l2",
            "dimensions": 768, "dtype": "float32", "metric": "cosine",
        }
        return sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


class E5Encoder:
    def __init__(self, model_dir: Path, space: E5Space, *, device: str = "cpu"):
        import torch
        from transformers import AutoModel, AutoTokenizer

        root = Path(model_dir).resolve(strict=True)
        if not space.revision or not space.files:
            raise ValueError("A pinned E5 revision and file checksums are required")
        for name, checksum in space.files.items():
            path = (root / name).resolve(strict=True)
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError("Invalid pinned model file")
            with path.open("rb") as source:
                if file_digest(source, "sha256").hexdigest() != checksum:
                    raise ValueError(f"E5 artifact checksum mismatch: {name}")
        if set(space.files) != {
            path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
        }:
            raise ValueError("E5 directory contains unpinned files")
        self.space = space
        self.torch = torch
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, trust_remote_code=False)
        self.model = AutoModel.from_pretrained(
            root, local_files_only=True, trust_remote_code=False, use_safetensors=True,
            dtype=torch.float32,
        ).to(device=device, dtype=torch.float32).eval()

    def encode(self, texts: list[str], *, role: str) -> list[list[float]]:
        if role not in {"query", "passage"}:
            raise ValueError("E5 requires an explicit retrieval role")
        return self.encode_batch([(role, text) for text in texts])

    def encode_batch(self, items: list[tuple[str, str]]) -> list[list[float]]:
        if any(role not in {"query", "passage"} for role, _ in items):
            raise ValueError("E5 requires an explicit retrieval role")
        if not items:
            return []
        inputs = self.tokenizer(
            [f"{role}: {text}" for role, text in items], max_length=512, padding=True,
            truncation=True, return_tensors="pt",
        ).to(self.device)
        with self.torch.inference_mode(), self.torch.autocast(self.model.device.type, enabled=False):
            vectors = self.forward_tokens(inputs)
            if (vectors.shape != (len(items), 768) or not self.torch.isfinite(vectors).all()
                    or not ((vectors.norm(dim=1) - 1).abs() <= 1e-5).all()):
                raise ValueError("Encoder produced an incompatible vector")
            return vectors.cpu().tolist()

    def forward_tokens(self, inputs):
        outputs = self.model(**inputs).last_hidden_state.float()
        mask = inputs["attention_mask"].bool().unsqueeze(-1)
        pooled = outputs.masked_fill(~mask, 0).sum(1) / mask.sum(1)
        return pooled / self.torch.linalg.vector_norm(pooled, dim=1, keepdim=True)
