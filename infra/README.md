# Infraestructura

Aquí viven los archivos que ejecuta cada servicio. Los clientes y conversiones de protocolo se mantienen en `src/search_retrieval/adapters/`.

| Ruta | Contenido previsto |
| --- | --- |
| [postgres](postgres/README.md) | Migraciones y operación de PostgreSQL. |
| [vespa](vespa/README.md) | Paquete de aplicación, esquemas y perfiles de ranking. |
| [object-storage](object-storage/README.md) | Configuración del almacenamiento y políticas de ciclo de vida. |
| [serving/vllm](serving/vllm/README.md) | Configuración del proceso de inferencia y sus recursos. |

Los archivos de despliegue se incorporan cuando exista un entorno objetivo y se puedan verificar el aislamiento de red, los recursos y las versiones elegidas.
