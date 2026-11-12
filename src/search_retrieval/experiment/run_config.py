"""Load one run configuration and resolve every identity it refers to."""

from dataclasses import dataclass
from pathlib import Path
import re
import tomllib

from search_retrieval.contracts.canonical import is_sha256, sealed_digest
from search_retrieval.contracts.identity import (
    GenerationKey, QuerySetKey, RunKey, SpaceKey, TransformKey,
)
from search_retrieval.experiment.manifests import ManifestError, inside, read_manifest, require


class ConfigError(ValueError):
    """The run configuration is incomplete, unknown or inconsistent."""


RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
PLACEHOLDER = re.compile(r"<[^<>]*>")

# name -> (required fields, optional fields); each field maps to its exact type.
SECTIONS = {
    "run": ({"id": str, "code_commit": str, "split": str}, {}),
    "corpus": ({"manifest": str}, {}),
    "transform": ({"manifest": str}, {}),
    "index": ({"engine": str, "schema_revision": str},
              {"hnsw": dict, "filter": dict, "lexical": dict, "insertion_seed": int}),
    "ranking": ({"profile": str, "target_hits": int, "fusion": dict}, {}),
    "queries": ({"set": str, "judgments": str, "manifest": str}, {}),
    "runtime": ({"manifest": str}, {}),
    "load": ({"arrival": str, "rates_qps": list, "measured_per_rate": int, "repeats": int}, {}),
}
OPTIONAL_SECTIONS = {"space", "load"}
SPACE = ({"encoder": str, "revision": str, "files": dict, "query_template": str, "doc_template": str,
          "pooling": str, "normalize": bool, "max_tokens": int, "dtype": str, "dim": int},
         {"mrl_dim": int, "quantization": str, "legacy_id": str, "adapter": str, "source": str})

# Each corpus and query-set format requires its own optional fields; the runner checks them.
CORPUS = ({"name": str, "source": str, "license": str, "format": str, "documents": int},
          {"archive": str, "archive_sha256": str, "corpus_member": str,
           "index_directory": str, "index_sha256": str, "ir_datasets": str, "documents_sha256": str})
TRANSFORM = {"parser": str, "parser_version": str, "configuration": dict, "chunking": dict, "tokenizer": str}
QUERY_SET = ({"name": str, "split": str, "judgments": str, "queries_sha256": str,
              "qrels_sha256": str, "queries": int},
             {"note": str, "queries_member": str, "qrels_member": str, "ir_datasets": str})
RUNTIME = ({"python": str, "platform": str, "locks": dict}, {"tools": dict})


@dataclass(frozen=True)
class ResolvedRun:
    path: str
    config: dict
    corpus: dict
    corpus_digest: str
    transform: TransformKey
    spaces: dict[str, SpaceKey]
    generation: GenerationKey
    query_set_manifest: dict
    query_set: QuerySetKey
    runtime: dict
    runtime_digest: str
    run: RunKey
    frozen_digest: str

    @property
    def split(self) -> str:
        return self.config["run"]["split"]

    def keys(self) -> dict:
        return {
            "corpus": self.corpus_digest, "transform": self.transform.digest,
            "spaces": {name: space.digest for name, space in sorted(self.spaces.items())},
            "generation": self.generation.digest, "query_set": self.query_set.digest,
            "runtime": self.runtime_digest, "run": self.run.digest, "frozen": self.frozen_digest,
        }


def _placeholders(value, path: str) -> None:
    if type(value) is str and PLACEHOLDER.fullmatch(value.strip()):
        raise ConfigError(f"Unfilled value at {path}: {value}")
    if isinstance(value, dict):
        for key, item in value.items():
            _placeholders(item, f"{path}.{key}")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _placeholders(item, f"{path}[{index}]")


def _section(name: str, table, schema) -> None:
    required, optional = schema
    if not isinstance(table, dict):
        raise ConfigError(f"[{name}] must be a table")
    unknown = set(table) - set(required) - set(optional)
    missing = set(required) - set(table)
    if unknown or missing:
        raise ConfigError(f"[{name}]: unknown {sorted(unknown)}, missing {sorted(missing)}")
    for field, value in table.items():
        if type(value) is not {**required, **optional}[field]:
            raise ConfigError(f"[{name}].{field} has the wrong type")


def parse_config(data: bytes) -> dict:
    """Validate the complete schema. Unknown sections and fields are errors."""
    try:
        config = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise ConfigError(f"Invalid run configuration: {error}") from error
    _placeholders(config, "$")
    unknown = set(config) - set(SECTIONS) - OPTIONAL_SECTIONS
    missing = set(SECTIONS) - OPTIONAL_SECTIONS - set(config)
    if unknown or missing:
        raise ConfigError(f"Unknown sections {sorted(unknown)}, missing {sorted(missing)}")
    for name, schema in SECTIONS.items():
        if name in config:
            _section(name, config[name], schema)
    spaces = config.get("space", {})
    if not isinstance(spaces, dict):
        raise ConfigError("[space] holds one table per named space")
    for name, table in spaces.items():
        # A space is described inline or taken from the registry by its manifest.
        if not (isinstance(table, dict) and set(table) == {"manifest"} and type(table["manifest"]) is str):
            _section(f"space.{name}", table, SPACE)
    run = config["run"]
    if not RUN_ID.fullmatch(run["id"]):
        raise ConfigError("[run].id may contain letters, digits, dot, underscore and hyphen")
    if "kind" not in config["ranking"]["fusion"]:
        raise ConfigError("[ranking].fusion must declare its kind")
    return config


def _typed(body: dict, kind: str, schema) -> None:
    required, optional = schema if isinstance(schema, tuple) else (schema, {})
    require(body, kind, required, optional)


def space_key(table: dict) -> SpaceKey:
    """Build the key of a space from its description. Registry annotations are not hashed."""
    return SpaceKey(
        encoder=table["encoder"], revision=table["revision"], files=table["files"],
        query_template=table["query_template"], document_template=table["doc_template"],
        pooling=table["pooling"], normalize=table["normalize"], max_tokens=table["max_tokens"],
        dtype=table["dtype"], dimensions=table["dim"],
        mrl_dimensions=table.get("mrl_dim", table["dim"]),
        quantization=table.get("quantization", "none"),
    )


def read_space(root: Path, relative: str) -> tuple[dict, SpaceKey]:
    """Load a registered space: its sealed description and its key."""
    body, _ = read_manifest(root, relative, "space")
    require(body, "space", *SPACE)
    try:
        return body, space_key(body)
    except ValueError as error:
        raise ManifestError(f"{relative}: {error}") from error


def resolve(root: Path, relative: str) -> ResolvedRun:
    """Build every key of the run. Each referenced manifest must match its own digest."""
    try:
        config = parse_config(inside(root, relative).read_bytes())
    except OSError as error:
        raise ConfigError(f"Unreadable run configuration: {relative}") from error
    try:
        corpus, corpus_digest = read_manifest(root, config["corpus"]["manifest"], "corpus")
        _typed(corpus, "corpus", CORPUS)
        if corpus["documents"] <= 0 or not all(
                is_sha256(corpus[name]) for name in ("archive_sha256", "index_sha256", "documents_sha256")
                if name in corpus):
            raise ManifestError("corpus: invalid digest or document count")
        transform_body, transform_digest = read_manifest(root, config["transform"]["manifest"], "transform")
        _typed(transform_body, "transform", TRANSFORM)
        transform = TransformKey(**transform_body)
        queries, _ = read_manifest(root, config["queries"]["manifest"], "query-set")
        _typed(queries, "query-set", QUERY_SET)
        runtime, runtime_digest = read_manifest(root, config["runtime"]["manifest"], "runtime")
        _typed(runtime, "runtime", RUNTIME)
        if not runtime["locks"] or not all(is_sha256(value) for value in runtime["locks"].values()):
            raise ManifestError("runtime: locks must map lock files to SHA-256 digests")
        query_set = QuerySetKey(queries["name"], queries["split"], queries["queries_sha256"],
                                queries["qrels_sha256"], queries["judgments"])
        if (queries["name"], queries["judgments"], queries["split"]) != (
                config["queries"]["set"], config["queries"]["judgments"], config["run"]["split"]):
            raise ConfigError("[queries] and [run].split disagree with the query-set manifest")
        spaces = {
            name: read_space(root, table["manifest"])[1] if set(table) == {"manifest"} else space_key(table)
            for name, table in config.get("space", {}).items()
        }
        digests = sorted(space.digest for space in spaces.values())
        if len(set(digests)) != len(digests):
            raise ConfigError("Two named spaces describe the same vector space")
        index = config["index"]
        generation = GenerationKey(
            corpus=corpus_digest, transform=transform.digest, spaces=tuple(digests),
            engine=index["engine"], schema_revision=index["schema_revision"],
            parameters={name: index[name] for name in ("hnsw", "filter", "lexical") if name in index},
            insertion_seed=index.get("insertion_seed"),
        )
        ranking = config["ranking"]
        run = RunKey(
            generation=generation.digest, ranking_profile=ranking["profile"],
            target_hits=ranking["target_hits"], fusion=ranking["fusion"], query_set=query_set.digest,
            code_commit=config["run"]["code_commit"], runtime=runtime_digest,
        )
    except ManifestError as error:
        raise ConfigError(str(error)) from error
    except ValueError as error:
        if isinstance(error, ConfigError):
            raise
        raise ConfigError(f"Invalid identity: {error}") from error
    assert transform.digest == transform_digest
    # Everything a development run may tune. Only the query set separates dev from test.
    frozen = sealed_digest("frozen-configuration", {
        name: value for name, value in run.body().items() if name != "query_set"
    })
    return ResolvedRun(relative, config, corpus, corpus_digest, transform, spaces, generation,
                       queries, query_set, runtime, runtime_digest, run, frozen)
