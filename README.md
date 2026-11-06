# Search, Retrieval e Inferencia

Sistema de ingesta documental, recuperación híbrida e inferencia con citas en construcción. El primer incremento implementa la aceptación durable de revisiones con PostgreSQL y un almacén de archivos persistente.

## Estructura

| Ruta | Responsabilidad |
| --- | --- |
| `src/search_retrieval/contracts/` | Identidades compartidas entre ingesta y consulta. |
| `src/search_retrieval/ingestion/` | Recepción de cambios, transformación, outbox e indexación. |
| `src/search_retrieval/query/` | Gateway, recuperación, ranking, contexto e inferencia. |
| `src/search_retrieval/adapters/` | Implementaciones de interfaces para servicios externos. |
| `config/` | Manifiesto de versión compatible y ubicación de configuración local. |
| `infra/` | Migraciones, aplicación Vespa, almacenamiento y serving. |
| `evaluation/` | Protocolo, datos sintéticos y manifiesto de ejecuciones independientes. |
| `tests/` | Pruebas unitarias, de contrato y de integración con PostgreSQL. |
| `docs/` | Límites de arquitectura, decisiones y procedimientos operativos. |

## Instalación y pruebas

Requiere Python 3.12 o posterior. Desde esta carpeta, ejecuta lo siguiente en PowerShell.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m unittest discover -s tests/unit -v
.\.venv\Scripts\python.exe -m unittest discover -s tests/contract -v
```

En macOS o Linux, el ejecutable del entorno virtual es `.venv/bin/python`. La instalación puede requerir descargar el backend de empaquetado. Las pruebas unitarias se ejecutan sin motores externos ni GPU.

## Alcance actual

El módulo de ingesta valida el original, registra una revisión y su evento de outbox, y recupera el mismo recibo en reintentos equivalentes. La [guía de ingesta](src/search_retrieval/ingestion/README.md) describe el uso desde Python y la preparación de PostgreSQL. La [guía de integración](tests/integration/README.md) explica cómo reproducir los ensayos de recuperación.

El perfil sintético añade reentrega desde un manifiesto, checkpoint, proyecciones Vespa y búsqueda con elegibilidad. Consulta el [contrato de incrementos](docs/contracts/witness-increments.md), la [matriz con evidencia](docs/verification/witness-increments.md) y los [comandos del laboratorio](docs/operations/witness-lab.md).

Consulta la [arquitectura](docs/architecture.md), las [decisiones pendientes](docs/decisions/README.md) y la [guía de contribución](CONTRIBUTING.md).
