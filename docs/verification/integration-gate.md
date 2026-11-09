# Verificación de integración y entrega

| Ejecución | Configuración | Cobertura |
| --- | --- | --- |
| `42262268040` | `324f937`, despacho WP-01/LX-01 | Publicación y recuperación léxica en el testigo con PostgreSQL y Vespa. [Run](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42262268040) |
| `42465181926` | `c1fd4fd`, despacho de estabilidad | Ensayo de estabilidad en la rama experimental. La auditoría de sus resultados pareados se registra por separado. [Run](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42465181926) |
| CPU integration gate | Push a una rama o pull request hacia `main` | Suites unitarias y de contrato, 12 ensayos de aceptación en PostgreSQL, 7 ensayos del testigo, incluyendo retiro léxico, denso e híbrido con Vespa y vectores sintéticos, y LX-01. |
| Real inference witness | Despacho con `real_inference=true` | Recorrido privado desde el original aceptado hasta la entrega con modelo real. Conserva evidencia, rechazo por ACL y fallo de backend. |

La puerta CPU y la corrida real identifican su commit y conservan los logs por ejecución. El workflow exige el conteo y estado terminal de las suites de integración. La selección CPU omite el ensayo de modelo real por nombre de prueba. [Workflow](../../.github/workflows/wp01.yml)

## RI-01 · Entrega privada con modelo real

La prueba usa el caso de uso de entrega privado y una identidad de investigador fijada por el arnés. Acepta un original UTF-8 sintético, publica la proyección en Vespa, marca el documento privado y consulta con un principal autorizado. El contador HTTP usa la misma plantilla que el servidor de inferencia. La API compatible con OpenAI sirve Qwen2.5-1.5B-Instruct Q4_K_M en llama.cpp b12211, con dos hilos, contexto de 1.024 y una solicitud en paralelo. Se fijan revisión y SHA-256 del GGUF y del binario Linux. [Prueba](../../tests/integration/test_witness_pipeline.py) · [API del runtime fijado](https://github.com/ggml-org/llama.cpp/blob/b12211/tools/server/README.md)

La primera corrida con generación libre llegó a la validación y fue rechazada porque el modelo devolvió una salida que el parser no pudo decodificar como JSON. El perfil del testigo pasó a solicitar JSON Schema con el identificador elegible como único valor de cita. La aplicación conserva la validación independiente de citas y el oráculo comprueba que el texto contenga el dato del original. [Corrida con generación libre](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42539627794)

| Condición | Resultado requerido |
| --- | --- |
| Principal autorizado | El modelo recupera el valor del original y devuelve una cita de los fragmentos efectivos. Se resuelve el identificador mediante el manifiesto publicado y se verifica el rango en bytes del original por SHA-256. |
| Otro principal | La autoridad y Vespa producen un conjunto vacío y la entrega se rechaza antes de generar. |
| Puerto de inferencia sin servicio | La entrega se rechaza y carece de respuesta privada. |
| Revocación al finalizar la generación | La última lectura de PostgreSQL detecta la revocación y descarta la respuesta completa. |

```sh
gh workflow run wp01.yml --ref main -f real_inference=true -f dense=false -f scifact=false
```

El ensayo acredita una ruta privada, léxica y sintética en el laboratorio. El servicio de destino requiere integrar un transporte autenticado y medir el comportamiento bajo carga. La publicación de PDF, el perfil de inferencia vLLM y la reescritura bibliográfica conservan sus criterios de verificación propios. [Objetivos](../operations/service-objectives.md)
