# Vespa

The application package binds the schema, fields, ranking profiles and services. Record the schema version with the index generation and vector space. [Publication decision](../../engineering/decisions/0001-publicacion.md) · [RRF baseline](../../engineering/decisions/0004-rrf.md)

## Managed runtime preflight

On Linux with `unshare`, `ip`, `timeout` and Python 3, run `bash infra/vespa/check-container-namespaces.sh`. It separately checks loopback inside a fresh network namespace and mounting `/proc` in a fresh PID namespace. Exit 0 means both checks passed, 1 means at least one failed and 2 means a tool is unavailable. The probe uses transient namespaces and binds inside them. After it passes, verify the pinned image with the selected OCI runtime.
