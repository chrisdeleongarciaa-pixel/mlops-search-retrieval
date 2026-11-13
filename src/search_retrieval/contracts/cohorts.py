"""Selectivity cohorts: which documents a filtered study treats as eligible.

A document belongs to the cohort of `permille` when its bucket is below `permille`. The bucket
depends on the document ID alone, so the FAISS oracle, the ANN sweeps and the engine
projection select exactly the same documents, and cohorts are nested: 0.1 % ⊂ 1 % ⊂ 10 % ⊂
50 % ⊂ 100 %.
"""

from hashlib import sha256

import numpy as np


COHORT_RULE = "sha256(b'cohort' + b'\\x00' + utf8(id)), first 8 bytes big-endian, mod 1000 < permille"
DOMAIN = b"cohort\x00"


def cohort_bucket(item: str) -> int:
    return int.from_bytes(sha256(DOMAIN + item.encode("utf-8")).digest()[:8], "big") % 1000


def check_permille(permille: int) -> None:
    if type(permille) is not int or not 0 < permille <= 1000:
        raise ValueError("A cohort is given in permille, from 1 to 1000")


def cohort_buckets(ids: list[str]) -> np.ndarray:
    """Bucket of every ID, computed once and reused for all cohorts."""
    return np.fromiter((cohort_bucket(item) for item in ids), dtype=np.uint16, count=len(ids))


def cohort_mask(buckets: np.ndarray, permille: int) -> np.ndarray:
    """Boolean mask of the documents in the cohort, from their buckets."""
    check_permille(permille)
    return buckets < permille
