# Bibliographic laboratory retention

| Artifact | Owner | Retention and removal condition | Verification |
| --- | --- | --- | --- |
| Public corpus, queries, qrels, rankings and locks | Lab operator | Keep while a maintained experiment or release uses them. Record license and provenance. | Reconstruct manifests and verify file hashes. |
| Private queries, prompts and answers | Service operator | Request lifetime. Release on completion, cancellation or expiration. Routine logs contain metadata. | Inspect target transport and buffers. |
| Operational traces | Lab operator | Keep with the run while needed as evidence. Content capture needs purpose, access scope and expiry. | Audit fields and artifacts. |
| Superseded index | Deployment operator | Keep while eligible for rollback or referenced by a reader. Retire after both references end. | Exercise drain, physical removal and reconciliation. |
| Tombstones, ACL and acceptance records | Authority operator | Keep while a replay, backup or maintained version can reference the revision. | Restore and verify exclusion before enabling queries. |

Set time windows and backup rules for the target profile and verify them before operation.
