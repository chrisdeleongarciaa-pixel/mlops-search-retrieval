"""Identidades inmutables para referenciar evidencia documental."""

from dataclasses import dataclass


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
