# Infrastructure

| Component | Configuration |
| --- | --- |
| [PostgreSQL](postgres/README.md) | Authority migrations and local Compose. |
| [Vespa](vespa/README.md) | Application, schema, rank profiles and startup checks. |
| [Object storage](object-storage/README.md) | File-backed originals and lifecycle constraints. |
| [GROBID](grobid/compose.yaml) | Pinned scientific PDF parser and resource limits. |
| [vLLM](serving/vllm/README.md) | Inference pilot and GPU preflight. |

Adapters live in the Python package. Deployment profiles identify effective image digests, resource limits and network scope. [Runbooks](../engineering/operations/README.md)
