"""Encode rendered texts for one registered vector space from its pinned model snapshot.

The space description decides everything that changes a vector: files, templates, pooling
path, token limit and normalization. Inference is FP32; the stored dtype is applied by the
vector store.
"""

from hashlib import file_digest
from pathlib import Path


OPTIONAL_FILES = {".gitattributes", "README.md", "notebook.ipynb"}
ADAPTERS = ("transformers-masked-mean", "sentence-transformers")


def render(template: str, title: str, text: str) -> str:
    """Fill a query or document template. An empty title renders as `none` where the template says so."""
    return (template.replace("{title or none}", title.strip() or "none")
            .replace("{title}", title).replace("{text}", text))


def verify_snapshot(directory: Path, files: dict[str, str]) -> Path:
    """Every pinned file matches its SHA-256 and nothing unpinned is present."""
    root = Path(directory).resolve(strict=True)
    present = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    if not files.keys() <= present or present - files.keys() - OPTIONAL_FILES:
        raise ValueError("The model directory is not the clean, complete snapshot of the space")
    for name, checksum in files.items():
        with (root / name).open("rb") as source:
            if file_digest(source, "sha256").hexdigest() != checksum:
                raise ValueError(f"Model file does not match the space: {name}")
    return root


class SpaceEncoder:
    """`body` is the sealed description of a registered space (see manifests/space)."""

    def __init__(self, body: dict, directory: Path, *, device: str = "cpu"):
        import torch

        if body.get("adapter") not in ADAPTERS:
            raise ValueError(f"No encoder implements the adapter {body.get('adapter')!r}")
        root = verify_snapshot(directory, body["files"])
        self.body, self.torch, self.device = body, torch, torch.device(device)
        self.max_tokens, self.dimensions = body["max_tokens"], body["dim"]
        if body["adapter"] == "sentence-transformers":
            from sentence_transformers import SentenceTransformer

            self.sentence_transformer = SentenceTransformer(
                str(root), device=device, trust_remote_code=False, local_files_only=True,
                model_kwargs={"dtype": torch.float32, "use_safetensors": True},
            ).float().eval()
            self.sentence_transformer.max_seq_length = self.max_tokens
            self.tokenizer = self.sentence_transformer.tokenizer
            self.model = None
        else:
            from transformers import AutoModel, AutoTokenizer

            self.sentence_transformer = None
            self.tokenizer = AutoTokenizer.from_pretrained(root, local_files_only=True, trust_remote_code=False)
            self.model = AutoModel.from_pretrained(
                root, local_files_only=True, trust_remote_code=False, use_safetensors=True, dtype=torch.float32,
            ).to(device=self.device, dtype=torch.float32).eval()

    def token_counts(self, texts: list[str]) -> list[int]:
        """Tokens of each rendered text before truncation, special tokens included."""
        return [len(ids) for ids in self.tokenizer(texts, verbose=False)["input_ids"]]

    def encode_texts(self, texts: list[str]):
        """Return one float32 row per rendered text, in input order."""
        torch = self.torch
        if not texts:
            raise ValueError("Nothing to encode")
        with torch.inference_mode(), torch.autocast(self.device.type, enabled=False):
            if self.sentence_transformer is not None:
                tokens = self.sentence_transformer.preprocess(texts)
                tokens = {name: value.to(self.device) if torch.is_tensor(value) else value
                          for name, value in tokens.items()}
                vectors = self.sentence_transformer(dict(tokens))["sentence_embedding"].float()
            else:
                inputs = self.tokenizer(
                    texts, max_length=self.max_tokens, padding=True, truncation=True, return_tensors="pt",
                ).to(self.device)
                hidden = self.model(**inputs).last_hidden_state.float()
                mask = inputs["attention_mask"].bool().unsqueeze(-1)
                vectors = hidden.masked_fill(~mask, 0).sum(1) / mask.sum(1)
            if self.body["normalize"]:
                vectors = torch.nn.functional.normalize(vectors, dim=1)
        if vectors.shape != (len(texts), self.dimensions) or not torch.isfinite(vectors).all():
            raise ValueError("The encoder produced rows of the wrong shape or non-finite values")
        return vectors.cpu().numpy().astype("float32", copy=False)
