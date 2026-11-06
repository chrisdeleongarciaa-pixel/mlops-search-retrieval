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
            root, local_files_only=True, trust_remote_code=False, use_safetensors=True
        ).to(device).eval()

    def encode(self, texts: list[str], *, role: str) -> list[list[float]]:
        if role not in {"query", "passage"}:
            raise ValueError("E5 requires an explicit retrieval role")
        if not texts:
            return []
        inputs = self.tokenizer(
            [f"{role}: {text}" for text in texts], max_length=512, padding=True,
            truncation=True, return_tensors="pt",
        ).to(self.device)
        with self.torch.inference_mode():
            outputs = self.model(**inputs).last_hidden_state.float()
            mask = inputs["attention_mask"].bool().unsqueeze(-1)
            pooled = outputs.masked_fill(~mask, 0).sum(1) / mask.sum(1)
            norms = self.torch.linalg.vector_norm(pooled, dim=1, keepdim=True)
            if (
                pooled.shape != (len(texts), 768)
                or not self.torch.isfinite(norms).all()
                or (norms == 0).any()
            ):
                raise ValueError("Encoder produced an incompatible vector")
            return (pooled / norms).cpu().tolist()
