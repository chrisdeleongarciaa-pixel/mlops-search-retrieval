"""Read access to a verified vector set: rows by global position, in their stored values.

Global position is the order of the collection, the order the set was written in. The shards
are memory-mapped, so a corpus larger than RAM can be read by row. Only float32 and float16
sets without quantization are readable here: they are the canonical rows of a dense space.
"""

from pathlib import Path

import numpy as np

from vector_store import STORED, VectorStoreError, _names, verify_vector_set


class CanonicalRows:
    def __init__(self, directory: Path):
        sealed = verify_vector_set(directory)
        body = sealed["body"]
        identity = body["identity"]
        if identity["dtype"] not in ("float32", "float16"):
            raise VectorStoreError("Exact search reads float32 or float16 rows, not quantized codes")
        self.directory, self.digest, self.identity = directory, sealed["sha256"], identity
        self.dtype, self.dimensions = identity["dtype"], identity["width"]
        self.ids: list[str] = []
        self.shards: list[np.ndarray] = []
        starts = [0]
        # verify_vector_set has just checked every shard; read IDs and map rows without rehashing.
        for ordinal in range(body["shards"]):
            names = _names(ordinal)
            ids = (directory / names["ids"]).read_bytes().decode("utf-8").splitlines()
            rows = np.load(directory / names["rows"], mmap_mode="r", allow_pickle=False)
            if rows.dtype != np.dtype(STORED[self.dtype]) or rows.shape != (len(ids), self.dimensions):
                raise VectorStoreError(f"Shard {ordinal} changed after verification")
            self.ids += ids
            self.shards.append(rows)
            starts.append(starts[-1] + len(ids))
        self.starts = np.array(starts, dtype=np.int64)
        self.n = int(self.starts[-1])
        if self.n != body["rows"] or len(set(self.ids)) != self.n:
            raise VectorStoreError("Vector set rows do not match its manifest or repeat an ID")
        # Rank of each row's ID in code point order: the tie break of every exact ranking.
        order = sorted(range(self.n), key=self.ids.__getitem__)
        self.id_rank = np.empty(self.n, dtype=np.int64)
        self.id_rank[np.array(order, dtype=np.int64)] = np.arange(self.n, dtype=np.int64)

    def close(self) -> None:
        """Release the memory maps. Windows cannot delete a file that is still mapped."""
        for rows in self.shards:
            mapping = getattr(rows, "_mmap", None)
            if mapping is not None:
                mapping.close()
        self.shards = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @property
    def space(self) -> str:
        return self.identity["space"]

    def take(self, rows: np.ndarray, dtype=np.float64) -> np.ndarray:
        """Rows at the given global positions, in the given order, converted exactly to `dtype`."""
        rows = np.asarray(rows, dtype=np.int64)
        if rows.ndim != 1 or (rows.size and (rows.min() < 0 or rows.max() >= self.n)):
            raise IndexError("Row positions outside the vector set")
        result = np.empty((rows.size, self.dimensions), dtype=dtype)
        shard_of = np.searchsorted(self.starts, rows, side="right") - 1
        for shard in np.unique(shard_of):
            where = np.nonzero(shard_of == shard)[0]
            result[where] = self.shards[shard][rows[where] - self.starts[shard]]
        return result

    def chunks(self, rows: np.ndarray, size: int = 65536, dtype=np.float32):
        """Yield `(positions, values)` for `rows` in the given order, a chunk at a time."""
        rows = np.asarray(rows, dtype=np.int64)
        for start in range(0, rows.size, size):
            part = rows[start:start + size]
            yield part, self.take(part, dtype)

    def max_norm(self, rows: np.ndarray | None = None) -> float:
        """Largest Euclidean norm, in float64, over the given rows (all rows by default)."""
        rows = np.arange(self.n, dtype=np.int64) if rows is None else rows
        largest = 0.0
        for _, values in self.chunks(rows, dtype=np.float64):
            if values.size:
                largest = max(largest, float(np.sqrt((values * values).sum(axis=1)).max()))
        return largest
