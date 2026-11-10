# Object storage

The first profile uses `FileObjectStore` with an existing private persistent directory. The filesystem must support hard links within that directory. Reuse the directory across restart and processes sharing the store.

The adapter writes and syncs a temporary file, checks its bytes and publishes by link while preserving an existing reference. Reuse verifies size and SHA-256. POSIX also syncs the directory. The Windows witness exercises process termination and restart with the same store.

Protect the directory from outside writes. Coordinate cleanup with in-flight acceptance and check live references. The current profile retains unreferenced files until a cleanup policy is verified.
