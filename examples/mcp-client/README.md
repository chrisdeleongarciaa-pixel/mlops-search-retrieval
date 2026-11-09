# MCP-01 · Integración de un modelo por API con un servidor MCP existente

El cliente usa el servidor upstream `@modelcontextprotocol/server-filesystem` por stdio y Qwen mediante una API local compatible con OpenAI. Descubre `read_text_file`, presenta una interfaz reducida al modelo, valida la solicitud de herramienta y devuelve su resultado al modelo para completar la respuesta. El expediente de prueba contiene texto sintético.

| Límite | Regla comprobada |
| --- | --- |
| Modelo | Recibe una única herramienta con el identificador lógico `brief`. El cliente rechaza cualquier otro nombre o argumento antes de emitir la llamada MCP. |
| Cliente | Resuelve el identificador a un archivo generado por el ensayo. Los paths que propone el modelo carecen de autoridad. |
| Servidor | Arranca con un directorio temporal permitido. Una llamada de control a un archivo sintético hermano verifica el rechazo fuera de esa raíz. |
| Transporte | El proceso local usa stdio y el modelo escucha en loopback. Las solicitudes HTTP tienen plazo. El cierre del cliente termina el transporte. |

Con Node.js y el modelo CPU del [ensayo de inferencia](../../docs/verification/integration-gate.md) escuchando como `qwen-witness` en el puerto `18090`, ejecutar desde esta carpeta.

```sh
npm ci --ignore-scripts
npm run verify
```

El lock fija el SDK, el servidor y sus dependencias. El ensayo requiere una llamada real a la herramienta y una respuesta final que recupere la retención indicada por el archivo. También verifica el rechazo de una operación de escritura propuesta, un alias inválido y una ruta fuera de la raíz. La lista permitida del cliente es la decisión de autorización. Las anotaciones del servidor describen herramientas. [SDK cliente](https://ts.sdk.modelcontextprotocol.io/v2/get-started/first-client) · [Servidor upstream](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem)
