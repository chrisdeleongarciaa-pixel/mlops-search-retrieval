"""Identidades inmutables para referenciar evidencia documental y experimental."""

from dataclasses import asdict, dataclass, field, fields
import re
from typing import ClassVar, Mapping

from search_retrieval.contracts.canonical import is_sha256, sealed_digest


@dataclass(frozen=True, slots=True)
class DocumentKey:
    """Identidad lógica de un documento dentro de una fuente y un tenant."""

    tenant_id: str
    source_id: str
    document_id: str


@dataclass(frozen=True, slots=True)
class RevisionKey:
    """Identidad de una revisión según la secuencia interna de su documento."""

    document: DocumentKey
    sequence: int


@dataclass(frozen=True, slots=True)
class ChunkKey:
    """Identidad de un fragmento dentro de una revisión y una transformación."""

    revision: RevisionKey
    transformation_id: str
    chunk_id: str


GIT_COMMIT = re.compile(r"[0-9a-f]{40}")
SPLITS = ("dev", "test", "regression")


def _text(name: str, value) -> None:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _digest(name: str, value) -> None:
    if not is_sha256(value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _positive(name: str, value) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


class _Sealed:
    """A key whose identity is the digest of its fields.

    The digest is fixed at construction. Mapping fields must not be mutated
    afterwards: equality and hashing follow the digest, not live field values.
    """

    KIND: ClassVar[str]

    def __post_init__(self):
        self._validate()
        object.__setattr__(self, "digest", sealed_digest(self.KIND, self.body()))

    def _validate(self) -> None:
        raise NotImplementedError

    def body(self) -> dict:
        return {item.name: getattr(self, item.name) for item in fields(self) if item.name != "digest"}

    def __eq__(self, other):
        return type(other) is type(self) and other.digest == self.digest

    def __hash__(self):
        return hash((self.KIND, self.digest))


@dataclass(frozen=True, eq=False)
class TransformKey(_Sealed):
    """Parser, its configuration, the chunking policy and the reference tokenizer."""

    KIND: ClassVar[str] = "transform"
    parser: str
    parser_version: str
    configuration: Mapping
    chunking: Mapping
    tokenizer: str
    digest: str = field(init=False)

    def _validate(self):
        for name in ("parser", "parser_version", "tokenizer"):
            _text(name, getattr(self, name))
        for name in ("configuration", "chunking"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"{name} must be a mapping")
        if not self.chunking:
            raise ValueError("chunking must declare its policy")


@dataclass(frozen=True, eq=False)
class ContentChunkKey(_Sealed):
    """A fragment addressed by its revision, transform, position and content.

    `ChunkKey` remains the identity of fragments that are already published.
    """

    KIND: ClassVar[str] = "chunk"
    revision: RevisionKey
    transform: str
    ordinal: int
    start: int
    end: int
    sha256: str
    digest: str = field(init=False)

    def _validate(self):
        if not isinstance(self.revision, RevisionKey) or not isinstance(self.revision.document, DocumentKey):
            raise ValueError("revision must be a RevisionKey")
        for name, value in asdict(self.revision.document).items():
            _text(name, value)
        _positive("sequence", self.revision.sequence)
        _digest("transform", self.transform)
        _digest("sha256", self.sha256)
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("ordinal must be a nonnegative integer")
        if type(self.start) is not int or type(self.end) is not int or not 0 <= self.start < self.end:
            raise ValueError("Expected a nonempty UTF-8 byte range")

    def body(self):
        return {**_Sealed.body(self), "revision": asdict(self.revision)}


@dataclass(frozen=True, eq=False)
class SpaceKey(_Sealed):
    """Everything that changes a stored vector: encoder files, prompts, pooling and numeric form."""

    KIND: ClassVar[str] = "space"
    encoder: str
    revision: str
    files: Mapping[str, str]
    query_template: str
    document_template: str
    pooling: str
    normalize: bool
    max_tokens: int
    dtype: str
    dimensions: int
    mrl_dimensions: int
    quantization: str
    digest: str = field(init=False)

    def _validate(self):
        for name in ("encoder", "revision", "pooling", "dtype", "quantization"):
            _text(name, getattr(self, name))
        # A template may be a bare prefix, so only its type and placeholder are constrained.
        for name in ("query_template", "document_template"):
            value = getattr(self, name)
            if type(value) is not str or "{text}" not in value:
                raise ValueError(f"{name} must contain the {{text}} placeholder")
        if not isinstance(self.files, dict) or not self.files:
            raise ValueError("files must pin at least one encoder artifact")
        for name, checksum in self.files.items():
            _text("file name", name)
            _digest(f"files[{name}]", checksum)
        if type(self.normalize) is not bool:
            raise ValueError("normalize must be a boolean")
        for name in ("max_tokens", "dimensions", "mrl_dimensions"):
            _positive(name, getattr(self, name))
        if self.mrl_dimensions > self.dimensions:
            raise ValueError("MRL truncation cannot exceed the encoder dimension")


@dataclass(frozen=True, eq=False)
class GenerationKey(_Sealed):
    """One searchable build: corpus snapshot, transform, spaces, engine schema and ANN parameters."""

    KIND: ClassVar[str] = "generation"
    corpus: str
    transform: str
    spaces: tuple[str, ...]
    engine: str
    schema_revision: str
    parameters: Mapping
    insertion_seed: int | None
    digest: str = field(init=False)

    def _validate(self):
        _digest("corpus", self.corpus)
        _digest("transform", self.transform)
        if type(self.spaces) is not tuple or list(self.spaces) != sorted(set(self.spaces)):
            raise ValueError("spaces must be a sorted tuple of distinct digests")
        for space in self.spaces:
            _digest("space", space)
        _text("engine", self.engine)
        _text("schema_revision", self.schema_revision)
        if not isinstance(self.parameters, dict):
            raise ValueError("parameters must be a mapping")
        if self.insertion_seed is not None and (type(self.insertion_seed) is not int or self.insertion_seed < 0):
            raise ValueError("insertion_seed must be a nonnegative integer")


@dataclass(frozen=True, eq=False)
class QuerySetKey(_Sealed):
    """Queries, judgments, their split and where the judgments come from."""

    KIND: ClassVar[str] = "query-set"
    name: str
    split: str
    queries_sha256: str
    qrels_sha256: str
    judgments: str
    digest: str = field(init=False)

    def _validate(self):
        _text("name", self.name)
        _text("judgments", self.judgments)
        if self.split not in SPLITS:
            raise ValueError(f"split must be one of {SPLITS}")
        _digest("queries_sha256", self.queries_sha256)
        _digest("qrels_sha256", self.qrels_sha256)


@dataclass(frozen=True, eq=False)
class RunKey(_Sealed):
    """One experiment. A change in any member is a different experiment."""

    KIND: ClassVar[str] = "run"
    generation: str
    ranking_profile: str
    target_hits: int
    fusion: Mapping
    query_set: str
    code_commit: str
    runtime: str
    digest: str = field(init=False)

    def _validate(self):
        for name in ("generation", "query_set", "runtime"):
            _digest(name, getattr(self, name))
        _text("ranking_profile", self.ranking_profile)
        _positive("target_hits", self.target_hits)
        if not isinstance(self.fusion, dict) or type(self.fusion.get("kind")) is not str:
            raise ValueError("fusion must declare its kind")
        if type(self.code_commit) is not str or not GIT_COMMIT.fullmatch(self.code_commit):
            raise ValueError("code_commit must be a full 40-hex Git commit")
