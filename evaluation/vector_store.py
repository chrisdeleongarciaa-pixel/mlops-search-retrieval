"""Canonical vectors outside any engine: hashed shards, resumable writes and derived forms.

A vector set is a directory of shards for one space, one role and one collection:

    shard-00000.npy      rows in collection order, stored in the dtype of the space
    shard-00000.ids      one ID per line, UTF-8, LF
    shard-00000.fp32.npy float32 rows of the parity sample (only when the space is not float32)
    shard-00000.json     sealed manifest, written last: a shard without it is incomplete
    manifest.json        sealed vector-set manifest, written when the collection ends

Hashes cover row bytes in little-endian C order, so they do not depend on the `.npy` header
or on how the rows were split into shards.
"""

from dataclasses import dataclass, replace
from hashlib import sha256
import json
import os
from pathlib import Path

import numpy as np

from search_retrieval.contracts.identity import SpaceKey
from search_retrieval.experiment.manifests import ManifestError, parse_manifest, seal


STORED = {"float32": "<f4", "float16": "<f2", "int8": "|i1"}
NORM_TOLERANCE = {"float32": 1e-5, "float16": 1e-3}
BINARY = "binary-sign-packbits"
INT8 = "int8-round127"
PARITY_MIN_COSINE = 0.9999


class VectorStoreError(ValueError):
    """A shard or vector set is incomplete, inconsistent or does not match its hashes."""


def stored_width(space: SpaceKey) -> int:
    """Columns of a stored row. Packed sign bits take one int8 per eight dimensions."""
    if space.quantization == BINARY:
        if space.mrl_dimensions % 8:
            raise VectorStoreError("Packed sign bits need a dimension divisible by eight")
        return space.mrl_dimensions // 8
    return space.mrl_dimensions


def check_space(space: SpaceKey) -> None:
    expected = "int8" if space.quantization in (BINARY, INT8) else space.dtype
    if space.dtype not in STORED or space.dtype != expected or (
            space.quantization not in ("none", BINARY, INT8)):
        raise VectorStoreError(f"Unsupported stored form: {space.dtype} with quantization {space.quantization}")


def row_bytes(rows: np.ndarray, dtype: str) -> bytes:
    return np.ascontiguousarray(rows, dtype=STORED[dtype]).tobytes()


def ids_bytes(ids: list[str]) -> bytes:
    if any(not item or item != item.strip() or "\n" in item or "\r" in item for item in ids):
        raise VectorStoreError("IDs must be nonempty single lines without surrounding whitespace")
    return "".join(f"{item}\n" for item in ids).encode("utf-8")


def in_parity_sample(item: str, every: int) -> bool:
    """A fixed sample chosen by ID alone, so it is the same on every run and every machine."""
    return int.from_bytes(sha256(item.encode("utf-8")).digest()[:8], "big") % every == 0


def _names(ordinal: int) -> dict[str, str]:
    stem = f"shard-{ordinal:05d}"
    return {"rows": f"{stem}.npy", "ids": f"{stem}.ids", "sample": f"{stem}.fp32.npy", "manifest": f"{stem}.json"}


def _write_atomic(path: Path, data: bytes) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _save_atomic(path: Path, rows: np.ndarray) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as target:
        np.save(target, rows, allow_pickle=False)
    os.replace(temporary, path)


def _sealed(path: Path, kind: str) -> tuple[dict, str]:
    try:
        return parse_manifest(path.read_bytes(), kind)
    except (OSError, ManifestError) as error:
        raise VectorStoreError(f"{path.name}: {error}") from error


@dataclass(frozen=True)
class Identity:
    """What a vector set contains: one space, one role and one collection."""

    space: SpaceKey
    role: str
    collection: dict

    def __post_init__(self):
        check_space(self.space)
        expected = {"document": {"corpus", "transform"}, "query": {"queries"}}
        if self.role not in expected or set(self.collection) != expected[self.role]:
            raise VectorStoreError("A document set names corpus and transform; a query set names its queries hash")

    def body(self) -> dict:
        return {"space": self.space.digest, "role": self.role, "collection": self.collection,
                "dtype": self.space.dtype, "width": stored_width(self.space)}


def read_shard(directory: Path, ordinal: int, identity_body: dict | None = None):
    """Load and verify one shard. Returns its manifest body, IDs, stored rows and float32 sample."""
    names = _names(ordinal)
    body, _ = _sealed(directory / names["manifest"], "vector-shard")
    if body["ordinal"] != ordinal or (identity_body is not None and body["identity"] != identity_body):
        raise VectorStoreError(f"{names['manifest']} belongs to another vector set or position")
    dtype, width = body["identity"]["dtype"], body["identity"]["width"]
    try:
        rows = np.load(directory / names["rows"], allow_pickle=False)
        ids = (directory / names["ids"]).read_bytes()
    except (OSError, ValueError) as error:
        raise VectorStoreError(f"Unreadable shard {ordinal}") from error
    if rows.dtype != np.dtype(STORED[dtype]) or rows.shape != (body["rows"], width):
        raise VectorStoreError(f"Shard {ordinal} has the wrong shape or dtype")
    if sha256(row_bytes(rows, dtype)).hexdigest() != body["rows_sha256"] or sha256(ids).hexdigest() != body["ids_sha256"]:
        raise VectorStoreError(f"Shard {ordinal} does not match its manifest")
    sample = None
    if body["sample_rows"] is not None:
        sample = np.load(directory / names["sample"], allow_pickle=False)
        if sample.dtype != np.dtype("<f4") or sha256(row_bytes(sample, "float32")).hexdigest() != body["sample_sha256"]:
            raise VectorStoreError(f"Parity sample of shard {ordinal} does not match its manifest")
    return body, ids.decode("utf-8").splitlines(), rows, sample


class VectorSetWriter:
    """Append shards in collection order. Complete shards found on disk are verified and kept."""

    def __init__(self, directory: Path, identity: Identity, *, shard_rows: int, parity_every: int | None = None,
                 derived_from: dict | None = None):
        if shard_rows <= 0 or (parity_every is not None and parity_every <= 0):
            raise VectorStoreError("Shard size and parity sampling interval must be positive")
        # Derived sets come from stored rows, so they have no float32 original to keep.
        if (parity_every is not None) != (identity.space.dtype == "float16" and derived_from is None):
            raise VectorStoreError("A parity sample is kept exactly for encoded float16 sets")
        self.directory, self.identity, self.shard_rows = directory, identity, shard_rows
        self.parity_every, self.derived_from = parity_every, derived_from
        self.shards: list[dict] = []
        self.ids_digest, self.rows_digest = sha256(), sha256()
        directory.mkdir(parents=True, exist_ok=True)
        if (directory / "manifest.json").exists():
            raise VectorStoreError(f"Vector set is already complete: {directory}")
        ordinal = 0
        while (directory / _names(ordinal)["manifest"]).exists():
            body, ids, rows, _ = read_shard(directory, ordinal, identity.body())
            if body["shard_rows"] != shard_rows or body["parity_every"] != parity_every:
                raise VectorStoreError("Resume with the shard size and parity interval of the existing shards")
            if body["rows"] != shard_rows and (directory / _names(ordinal + 1)["manifest"]).exists():
                raise VectorStoreError("Only the last shard may be short")
            self._account(body, ids, rows)
            ordinal += 1
        known = {name for done in range(ordinal) for name in _names(done).values()}
        for leftover in directory.iterdir():
            # Files of a shard that never received its manifest, or interrupted temporaries.
            if leftover.name.startswith("shard-") and leftover.name not in known:
                leftover.unlink()

    def _account(self, body: dict, ids: list[str], rows: np.ndarray) -> None:
        self.shards.append(body)
        self.ids_digest.update(ids_bytes(ids))
        self.rows_digest.update(row_bytes(rows, self.identity.space.dtype))

    @property
    def rows(self) -> int:
        return sum(shard["rows"] for shard in self.shards)

    def existing_ids_sha256(self) -> list[tuple[int, str]]:
        """Row count and ID hash of each kept shard, to check that the collection did not change."""
        return [(shard["rows"], shard["ids_sha256"]) for shard in self.shards]

    def append(self, ids: list[str], vectors: np.ndarray, truncated: int) -> None:
        """Store one shard from float32 rows, or int8 rows for a quantized space.

        `truncated` counts the inputs of this shard that exceeded the token limit of the space.
        """
        space = self.identity.space
        if self.shards and self.shards[-1]["rows"] != self.shard_rows:
            raise VectorStoreError("A short shard ends the vector set")
        if not 0 < len(ids) <= self.shard_rows or len(set(ids)) != len(ids):
            raise VectorStoreError("A shard holds between one and shard_rows distinct IDs")
        width = stored_width(space)
        sample = None
        if space.quantization == "none":
            if vectors.dtype != np.float32 or vectors.shape != (len(ids), width) or not np.isfinite(vectors).all():
                raise VectorStoreError("Expected finite float32 rows of the stored width")
            rows = vectors.astype(STORED[space.dtype])
            if space.normalize:
                norms = np.linalg.norm(rows.astype(np.float64), axis=1)
                if not (np.abs(norms - 1) <= NORM_TOLERANCE[space.dtype]).all():
                    raise VectorStoreError("Stored rows are not unit length")
            if self.parity_every is not None:
                keep = [index for index, item in enumerate(ids) if in_parity_sample(item, self.parity_every)]
                sample = np.ascontiguousarray(vectors[keep], dtype="<f4")
        else:
            if vectors.dtype != np.int8 or vectors.shape != (len(ids), width):
                raise VectorStoreError("Expected int8 rows of the stored width")
            rows = vectors
        if type(truncated) is not int or not 0 <= truncated <= len(ids):
            raise VectorStoreError("The truncation count must lie between zero and the shard rows")
        ordinal, names = len(self.shards), _names(len(self.shards))
        body = {
            "identity": self.identity.body(), "ordinal": ordinal, "rows": len(ids),
            "shard_rows": self.shard_rows, "parity_every": self.parity_every,
            "rows_sha256": sha256(row_bytes(rows, space.dtype)).hexdigest(),
            "ids_sha256": sha256(ids_bytes(ids)).hexdigest(), "truncated": truncated,
            "sample_rows": None if sample is None else len(sample),
            "sample_sha256": None if sample is None else sha256(row_bytes(sample, "float32")).hexdigest(),
        }
        _save_atomic(self.directory / names["rows"], np.ascontiguousarray(rows, dtype=STORED[space.dtype]))
        _write_atomic(self.directory / names["ids"], ids_bytes(ids))
        if sample is not None:
            _save_atomic(self.directory / names["sample"], sample)
        _write_atomic(self.directory / names["manifest"],
                      (json.dumps(seal("vector-shard", body), indent=2) + "\n").encode("utf-8"))
        self._account(body, ids, rows)

    def finish(self, expected_rows: int | None = None) -> dict:
        """Seal the set. The collection must be complete."""
        if not self.shards or (expected_rows is not None and self.rows != expected_rows):
            raise VectorStoreError(f"Vector set has {self.rows} rows, expected {expected_rows}")
        body = {
            "identity": self.identity.body(), "rows": self.rows, "shards": len(self.shards),
            "ids_sha256": self.ids_digest.hexdigest(), "rows_sha256": self.rows_digest.hexdigest(),
            "truncated": sum(shard["truncated"] for shard in self.shards),
            "parity_every": self.parity_every, "derived_from": self.derived_from,
        }
        sealed = seal("vector-set", body)
        _write_atomic(self.directory / "manifest.json", (json.dumps(sealed, indent=2) + "\n").encode("utf-8"))
        return sealed


def verify_vector_set(directory: Path) -> dict:
    """Recompute every shard hash and the set hashes. Returns the sealed set manifest."""
    body, digest = _sealed(directory / "manifest.json", "vector-set")
    ids_digest, rows_digest, rows, truncated = sha256(), sha256(), 0, 0
    for ordinal in range(body["shards"]):
        shard, ids, stored, _ = read_shard(directory, ordinal, body["identity"])
        ids_digest.update(ids_bytes(ids))
        rows_digest.update(row_bytes(stored, body["identity"]["dtype"]))
        rows, truncated = rows + shard["rows"], truncated + shard["truncated"]
    if (directory / _names(body["shards"])["manifest"]).exists():
        raise VectorStoreError("The directory holds more shards than its manifest")
    if (rows, truncated, ids_digest.hexdigest(), rows_digest.hexdigest()) != (
            body["rows"], body["truncated"], body["ids_sha256"], body["rows_sha256"]):
        raise VectorStoreError("Shards do not add up to the vector-set manifest")
    return {"kind": "vector-set", "schema_version": 1, "body": body, "sha256": digest}


def iterate_shards(directory: Path):
    """Yield `(ids, stored rows, float32 sample)` of a verified, complete vector set."""
    body = verify_vector_set(directory)["body"]
    for ordinal in range(body["shards"]):
        _, ids, rows, sample = read_shard(directory, ordinal, body["identity"])
        yield ids, rows, sample


# Derived forms. Each is computed from stored canonical rows, never by encoding again.

def truncate_mrl(rows: np.ndarray, dimensions: int) -> np.ndarray:
    """Keep the leading dimensions and restore unit length."""
    kept = rows[:, :dimensions].astype(np.float64)
    norms = np.linalg.norm(kept, axis=1, keepdims=True)
    if not (norms > 0).all():
        raise VectorStoreError("A truncated row has zero length")
    return (kept / norms).astype(np.float32)


def pack_signs(rows: np.ndarray) -> np.ndarray:
    """One bit per dimension, set when the value is positive, most significant bit first."""
    return np.packbits(rows > 0, axis=1).view(np.int8)


def round_int8(rows: np.ndarray) -> np.ndarray:
    """Scale unit-length components to [-127, 127] and round half to even."""
    return np.clip(np.rint(rows.astype(np.float64) * 127), -127, 127).astype(np.int8)


def derived_space(parent: SpaceKey, operation: dict) -> SpaceKey:
    """The space of a derived form. Only numeric form changes; the encoder identity is kept."""
    kind = operation.get("kind")
    if parent.quantization != "none":
        raise VectorStoreError("Quantized rows are not a source for further derivation")
    if kind == "mrl" and set(operation) == {"kind", "dimensions"}:
        if type(operation["dimensions"]) is not int or not 0 < operation["dimensions"] < parent.mrl_dimensions:
            raise VectorStoreError("MRL truncation must shorten the parent")
        return replace(parent, mrl_dimensions=operation["dimensions"])
    if kind == "binary" and set(operation) == {"kind"}:
        return replace(parent, dtype="int8", quantization=BINARY)
    if kind == "int8" and set(operation) == {"kind"}:
        if not parent.normalize:
            raise VectorStoreError("int8 rounding assumes unit-length rows")
        return replace(parent, dtype="int8", quantization=INT8)
    raise VectorStoreError(f"Unknown derivation: {operation}")


def derive(source: Path, destination: Path, parent: SpaceKey, operation: dict) -> dict:
    """Write the derived vector set with the IDs, order and shard boundaries of its parent."""
    manifest = verify_vector_set(source)
    body = manifest["body"]
    if body["identity"]["space"] != parent.digest:
        raise VectorStoreError("The source vector set belongs to another space")
    space = derived_space(parent, operation)
    transform = {"mrl": lambda rows: truncate_mrl(rows, operation["dimensions"]),
                 "binary": pack_signs, "int8": round_int8}[operation["kind"]]
    identity = Identity(space, body["identity"]["role"], body["identity"]["collection"])
    shard_rows = _sealed(source / _names(0)["manifest"], "vector-shard")[0]["shard_rows"]
    writer = VectorSetWriter(
        destination, identity, shard_rows=shard_rows,
        derived_from={"vector_set": manifest["sha256"], "operation": operation},
    )
    if writer.rows:
        raise VectorStoreError("Derive into an empty directory")
    for ordinal in range(body["shards"]):
        shard, ids, rows, _ = read_shard(source, ordinal, body["identity"])
        writer.append(ids, transform(rows.astype(np.float32)), shard["truncated"])
    return writer.finish(body["rows"])


def top_k_indices(queries: np.ndarray, documents: np.ndarray, ids: list[str], k: int):
    """Exact inner-product ranking in float64 with ties broken by document ID.

    Returns the score matrix and, per query, the column indices of its top k.
    """
    scores = queries.astype(np.float64) @ documents.astype(np.float64).T
    order = np.argsort(np.array(ids), kind="stable")
    best = [order[np.argsort(-row[order], kind="stable")[:k]] for row in scores]
    return scores, best


def top_k(queries: np.ndarray, documents: np.ndarray, ids: list[str], k: int) -> list[list[str]]:
    _, best = top_k_indices(queries, documents, ids, k)
    return [[ids[index] for index in row] for row in best]


def parity_report(documents: Path, queries: Path, *, k: int = 10) -> dict:
    """Compare stored float16 rows with the float32 rows kept for the fixed sample.

    Passes when every sampled row has cosine >= 0.9999 with its float32 original and every
    query has the same top-k over the sample in both precisions.

    The remaining fields describe the queries whose lists differ and do not affect the
    verdict: how much of the exact top-k the stored rows still return, and the largest
    float32 score difference between the documents found at the same rank in both lists.
    A small gap means near-tied neighbours changed places.
    """
    body = verify_vector_set(documents)["body"]
    every = body["parity_every"]
    if every is None:
        raise VectorStoreError("This vector set keeps no parity sample")
    sample_ids, stored, original = [], [], []
    for ids, rows, sample in iterate_shards(documents):
        keep = [index for index, item in enumerate(ids) if in_parity_sample(item, every)]
        if sample is None or len(keep) != len(sample):
            raise VectorStoreError("Parity sample does not match the sampling rule")
        sample_ids += [ids[index] for index in keep]
        stored.append(rows[keep])
        original.append(sample)
    stored_rows, original_rows = np.concatenate(stored), np.concatenate(original)
    if len(sample_ids) < k:
        raise VectorStoreError("The parity sample is smaller than the ranking depth")
    a, b = stored_rows.astype(np.float64), original_rows.astype(np.float64)
    cosines = (a * b).sum(axis=1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
    query_rows = np.concatenate([rows for _, rows, _ in iterate_shards(queries)]).astype(np.float32)
    scores, exact = top_k_indices(query_rows, original_rows, sample_ids, k)
    _, observed = top_k_indices(query_rows, stored_rows, sample_ids, k)
    identical, overlaps, gaps = 0, [], []
    for row, left, right in zip(scores, exact, observed, strict=True):
        overlaps.append(len(set(left.tolist()) & set(right.tolist())) / k)
        if (left == right).all():
            identical += 1
        else:
            gaps.append(float(np.abs(row[left] - row[right]).max()))
    return {
        "sample_rows": len(sample_ids), "sample_every": every, "queries": len(query_rows), "k": k,
        "min_cosine": float(cosines.min()), "identical_top_k": identical,
        "mean_overlap_at_k": float(np.mean(overlaps)), "min_overlap_at_k": float(np.min(overlaps)),
        "max_exact_score_gap_at_differing_ranks": max(gaps) if gaps else 0.0,
        "limits": {"min_cosine": PARITY_MIN_COSINE, "identical_top_k": len(query_rows)},
        "passed": bool(cosines.min() >= PARITY_MIN_COSINE and identical == len(query_rows)),
    }
