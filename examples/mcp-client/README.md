# MCP-01 · Model API with an existing MCP server

The client uses upstream `@modelcontextprotocol/server-filesystem` over stdio and calls Qwen through a local compatible model API. It discovers `read_text_file`, exposes a bounded tool definition, validates the proposal and returns the tool result to the model. The test uses synthetic files.

| Boundary | Rule |
| --- | --- |
| Model | One read tool with the logical document ID `brief`. Reject all other names and arguments before an MCP call. |
| Client | Map the authorized ID to the test file. The model never supplies the filesystem path used in the call. |
| Server | Restrict access to one temporary directory. Check rejection of a sibling file outside the allowed root. |
| Transport | Run the server over stdio and inference on loopback. Bound model requests and close the client transport after the run. |

Use Node.js 24 or later. Actions pins 25.9.0. Start llama.cpp b12211 with the verified GGUF from [RI-01](../../docs/verification/integration-gate.md).

```sh
llama-server --model /path/to/qwen.gguf --alias qwen-witness --host 127.0.0.1 \
  --port 18090 --threads 2 --ctx-size 1024 --parallel 1 --jinja
```

When `/health` returns `ok`, run from this directory.

```sh
npm ci --ignore-scripts
npm run verify
```

The test requires a real tool call and the retention value stated in the file. It also exercises rejected writes, aliases and paths. Client policy grants permission and upstream tool annotations describe capabilities. [Official client SDK](https://ts.sdk.modelcontextprotocol.io/v2/get-started/first-client) · [Official filesystem server](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
