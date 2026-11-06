# Contratos y entorno reproducible — Learned Retrieval & Representation Learning

Especificación candidata para la plataforma del [diseño técnico](https://chatgpt.com/library?file_id=libfile_9204f7a1d85c819192e4dbdc0e4e2537&account_id=personal). Incluye contratos de interfaces y estado, un corpus testigo bilingüe, resultados esperados, un oráculo ejecutable sin dependencias externas y revisiones fijadas de los evaluadores oficiales. Los nombres de campos y las decisiones de implementación son del proyecto. Las métricas oficiales conservan sus protocolos de origen.

## 1. Alcance y decisiones que condicionan la implementación

| Aspecto | Valor o decisión |
|---|---|
| Dominio | Learned Retrieval & Representation Learning |
| Idiomas del sistema | Inglés y español. La cobertura de cada benchmark se informa por separado |
| Hardware declarado | A100 con 40 GB de VRAM y host con 80 GB de RAM. Se interpretan los 190 GB como almacenamiento nominal pendiente de confirmar con espacio libre efectivo |
| Autoridad y proyecciones | PostgreSQL registra revisiones, publicación y elegibilidad. El almacenamiento de objetos conserva bytes inmutables. Vespa proyecta texto y vectores. El gateway aplica autorización y controla vLLM |
| Evaluación base | MS MARCO Passage Ranking v1, BEIR, BRIGHT, SciFact y SCIDOCS |
| Fuentes documentales | arXiv, S2ORC y peS2o. Seleccionar versión y subconjunto conforme a la capacidad medida. Los juicios de los benchmarks se aplican a su colección oficial |
| Decisiones de esta especificación | Proyección Vespa inmutable por revisión y generación. Publicación mediante actualización condicional del estado autorizado. Revalidación de elegibilidad antes de entregar resultados o contexto |
| Pendiente del entorno de destino | SO, arquitectura, driver, sistema de archivos, espacio libre, modalidad de acceso a GPU, backend de objetos, imágenes y modelos compatibles |
| Pendiente de política | Vigencia de grupos y permisos del proveedor de identidad, semántica de revocación durante un stream activo, retención, latencia y carga de aceptación |

Las reglas de seguridad de abajo definen un perfil local de pruebas. Antes de habilitar documentos privados con streams activos, se debe adoptar la política de revocación correspondiente. Las mediciones de calidad y rendimiento quedan ligadas al perfil que efectivamente se ejecute.

## 2. Tipos y fronteras de confianza

Las interfaces se especifican por nombres descriptivos. Una implementación puede usar mensajes internos o HTTP conservando estas precondiciones y postcondiciones.

| Tipo lógico | Campos obligatorios y restricciones |
|---|---|
| Clave documental | `tenant`, `source`, `document`. Tupla de identidad estable. Un hash compartido entre documentos no fusiona identidades ni permisos |
| Evento de origen | `schema_version`, `source_event_id`, clave documental, `op` de alta/actualización o retiro, versión de origen y token de orden según el conector. Para contenido incluye referencia del objeto, versión de objeto, tamaño y SHA‑256 |
| Orden de origen | Dominio de orden y comparador declarados por conector. Un cursor de entrega sirve para checkpoint. Un ETag opaco sirve para identidad/comparación de igualdad |
| Revisión interna | Clave documental, secuencia interna monótona por documento, versión de origen, operación, referencia a bytes y sello de aceptación. Se asigna en la transacción de aceptación |
| Artefacto de transformación | Revisión padre, versión de parser/chunker y config, SHA‑256 de entrada/salida, lista ordenada de fragmentos y motivo de cualquier exclusión |
| Fragmento | Clave de revisión, identidad de transformación, ordinal, texto y SHA‑256. Incluir unidad de offsets, rango semiabierto, referencia a texto canónico y localizador original cuando exista |
| Espacio de representación | Pesos/revisión, tokenizer, prefijos de query/documento, pooling, truncamiento, dimensión, normalización, representación almacenada y métrica. Identidad por manifiesto canónico versionado |
| Proyección | Clave documental, revisión, generación de índice, manifiesto de transformación, perfil de representación y hash del payload. Clave de índice única para esa combinación |
| Identidad de consulta | Principal, tenant y grupos obtenidos del contexto autenticado, con revisión/instante de la política que los respalda |
| Candidato | Clave de proyección y, si procede, fragmento, rama, score finito, rango, generación y estado de cobertura. Texto y metadatos sensibles viajan únicamente dentro del perímetro autorizado |
| Ejecución de consulta | `request_id`, deadline, perfil efectivo, snapshot de configuración, política, resultados por etapa y estado terminal. Trazas ordinarias identifican artefactos sin almacenar texto sensible |

Usar una codificación canónica explícita antes de calcular hashes. Los spans deben comprobarse sobre la representación cuya unidad se declaró. Un offset de bytes UTF‑8, uno de caracteres Unicode y uno de tokens tienen contratos distintos.

## 3. Estado documental y significado de las confirmaciones

**Aceptado** significa validación del sobre y admisión de la operación. En esta propuesta es un estado interno transitorio. **Durable** significa que el original inmutable ya fue confirmado y que PostgreSQL confirmó la transacción que persiste la revisión y su outbox. El recibo de aceptación externo se emite a partir de ese momento. La durabilidad se declara bajo el modelo de fallos ensayado y la configuración efectiva de ambos almacenes.

**Publicado** significa que se verificó la proyección en el recorrido de lectura previsto y que la autoridad confirmó el puntero a esa revisión y generación. **Elegible** es una decisión por consulta, principal, revisión de política e instante de lectura. Una proyección puede existir físicamente y quedar fuera de todas las respuestas.

| Dimensión de estado | Valores observables | Transición que autoriza el cambio |
|---|---|---|
| Recepción | Validado, durable, rechazado, resultado incierto para el cliente | Commit del recibo y su transacción. Si el cliente pierde la respuesta, consulta o repite la misma clave |
| Trabajo derivado | Pendiente, en proceso, proyectado, cuarentena, obsoleto | Intentos versionados y registro de causa. La cuarentena conserva el diagnóstico de la revisión durable |
| Cabeza del documento | Revisión deseada, puntero publicado opcional, retiro y fence | Actualización transaccional de la autoridad |
| Visibilidad por consulta | Elegible, inelegible, decisión no disponible | Lectura conjunta de cabeza, generación activa y política aplicable |
| Entrega | Pendiente, con datos, terminal o transporte interrumpido | Contrato del gateway y del consumidor |

### Aceptación de eventos

1. Resolver identidad y autoridad de la fuente. Validar versión de sobre, orden y límites. Para una fuente sin orden comparable, se debe definir su mecanismo de reconciliación antes de habilitar procesamiento fuera de orden.
2. Para un alta o actualización, escribir bytes bajo una referencia inmutable y verificar identidad, longitud y checksum. El retiro solo requiere el evento y la referencia a la identidad documental.
3. En una transacción PostgreSQL, aplicar unicidad de la clave idempotente `(tenant, source, source_event_id)`, bloquear o actualizar condicionalmente la cabeza y comprobar el orden de origen. Registrar recibo, revisión aceptada y outbox de forma atómica. Un evento obsoleto puede registrarse como descartado y no modifica la cabeza.
4. Repetir la misma clave y el mismo contenido semántico devuelve el recibo original. Reutilizarla con otro payload produce conflicto. Una pérdida de conexión durante el commit devuelve resultado incierto hasta consultar la clave.
5. Avanzar el checkpoint de entrega solo sobre un prefijo contiguo resuelto. Una operación durable pendiente de indexar puede estar resuelta para el conector y seguir pendiente para el consumidor de outbox.

**Fallo testigo.** Escribir el objeto y caer antes del commit deja un objeto huérfano. Caer después del commit y antes de responder deja un recibo recuperable y trabajo pendiente. Para el laboratorio, fijar la configuración de PostgreSQL que exige persistencia local de WAL antes de informar éxito y registrar el contrato real del almacenamiento de objetos. Un ensayo de reinicio de proceso y uno de pérdida de almacenamiento cubren modelos de fallo distintos. [Código fuente de configuración de PostgreSQL, `synchronous_commit`](https://github.com/postgres/postgres/blob/REL_18_STABLE/doc/src/sgml/config.sgml).

### Proyección y publicación

1. El indexador consume la outbox con reentregas esperadas. Transforma la revisión, produce el manifiesto completo y escribe una proyección por revisión y generación. Un reintento de la misma clave debe llevar el mismo hash de payload. Un cambio de parser, embeddings o esquema se materializa en una generación distinta.
2. Comprobar que la proyección puede recuperarse por su identidad, revisión y generación mediante la ruta de lectura que se pretende habilitar. Registrar la evidencia. Un ACK de escritura acredita la operación del motor bajo su configuración. La disponibilidad en la ruta de búsqueda se comprueba por separado.
3. Confirmar el puntero publicado en PostgreSQL mediante una condición que compruebe que siguen vigentes revisión deseada, fence, ausencia de retiro y generación de publicación prevista. Al perder la condición, la proyección queda huérfana u obsoleta. Confirmar el consumo del evento después de persistir el resultado recuperable.
4. Para una actualización ordinaria, el perfil local conserva la última publicación elegible mientras se prepara la nueva. El retiro elimina elegibilidad en la autoridad al confirmar su transacción. Un restore o una reentrega antigua debe reconciliarse con esa autoridad antes de reabrir el servicio.
5. Si en una variante se usa una fila de Vespa por fragmento, la publicación exige verificar el conjunto completo esperado por manifiesto. Ningún fragmento parcial puede hacerse elegible solo porque su escritura individual respondió.

**Carrera que el contrato debe detectar.** Empieza la transformación de la revisión 1, se confirma el retiro 2 y luego termina el trabajo de la revisión 1. El índice puede recibir esa proyección antigua. El CAS impide publicarla y la revalidación impide entregarla incluso si una consulta obtuvo el candidato antes del retiro.

### Lectura y autorización

En el perfil de laboratorio, la autoridad es una lectura consistente de PostgreSQL sobre cabeza, generación activa y ACL, con identidad resuelta por el gateway. La decisión final se toma antes de construir contexto o emitir resultados de búsqueda. Toda revisión que se incluya debe coincidir con el puntero publicado y satisfacer la política para ese principal. Si la decisión de autoridad falla, la solicitud termina con error sin emitir contenido basado en esa decisión.

El filtro inicial de Vespa usa tenant y permisos proyectados. La revalidación posterior cubre desfases de esa proyección. Una lectura de caché pasa por la misma comprobación de elegibilidad antes de entregar contenido. Cuando un procesador queda fuera del perímetro autorizado, revalidar antes de enviarle texto o metadatos. El instante de autorización se registra para poder probar carreras. Este perfil cubre el caso en que la revocación se confirma antes de esa lectura. La cancelación de streams ya iniciados tras una revocación requiere un contrato temporal adicional.

## 4. Contratos de interfaces

| Interfaz | Precondición a comprobar | Postcondición observable | Fallos que conserva |
|---|---|---|---|
| `submit_event` → recibo | Sobre válido, fuente autenticada, referencia de bytes verificada para contenido | Recibo durable identificable por clave. Duplicado equivalente devuelve el mismo resultado | Entrada inválida, conflicto idempotente, evento obsoleto, aceptación incierta |
| `transform_revision` → manifiesto | Bytes de la revisión durable, hash y versiones de transformación fijados | Fragmentos con identidad, offsets y procedencia. Salida reproducible dentro de la tolerancia declarada | Corrupción, límite excedido, formato no admitido, fallo temporal |
| `encode` → representaciones | Texto y perfil de espacio válidos, rol query/documento explícito | Vector/tensor con tipo, dimensión y valores finitos. Correspondencia uno a uno entre entradas y salidas | Espacio incompatible, norma cero para cosine, truncamiento observable, fallo de inferencia |
| `publish_projection` → publicación | Manifiesto completo, revisión e índice esperados | Evidencia de visibilidad y puntero confirmado, o resultado obsoleto sin cambio de elegibilidad | ACK incierto, proyección parcial, CAS perdido, autoridad indisponible |
| `retrieve` → candidatos por rama | Consulta validada, perfil permitido, identidad y presupuesto. Si se usa vector, espacio y generación compatibles | Identidades válidas, scores finitos y cobertura explícita por rama. Conservar profundidad pedida y efectiva | Índice no disponible, incompatibilidad, timeout, cobertura parcial, vacío válido |
| `fuse` / `rerank` → candidatos ordenados | Candidatos de la misma consulta y configuración. Reranker recibe pares identificados, con permiso de procesamiento | Identidad conservada, ausencia de duplicados y correspondencia exacta entre scores y pares incluso si cambia el orden del batch | Score no finito, respuesta incompleta, par duplicado, timeout |
| `assemble_context` → prompt y manifiesto de evidencia | Revalidación de elegibilidad, presupuesto y tokenizer/chat template del LLM | Evidencia retenida y descartada con motivo. Cada cita resoluble. Tokens del prompt efectivo más reserva de salida dentro del límite | Autoridad no disponible, contexto excedido, evidencia insuficiente |
| `generate` → stream | Modelo, tokenizer y template compatibles. Admisión por capacidad y deadline restante | Eventos en orden y un estado terminal interno. Propagación de cancelación y registro de recursos liberados | Saturación, timeout, fallo de backend, desconexión, terminación parcial |

**Errores de frontera.** Cada fallo lleva `code`, `stage`, `request_id`, `retryable` y, cuando haga falta, `outcome_unknown`. Propuesta de mapeo HTTP antes del stream: entrada inválida → 400, conflicto idempotente → 409, saturación → 429, dependencia no disponible → 503, deadline agotado → 504. Al iniciar un stream, un fallo posterior se expresa como terminación de error si el transporte sigue abierto. El servidor registra la desconexión como terminal aunque el cliente no pueda recibir el evento. Una respuesta sin hits es un resultado completo solo si las dependencias necesarias respondieron.

**Reintentos.** Respetan el deadline original y el ámbito de autorización. Un reintento de generación posterior a la emisión de tokens requiere un protocolo de reanudación explícito. El perfil inicial registra la respuesta parcial y cierra la operación.

## 5. Datos de evaluación y atribución de mejoras

### Matriz de protocolos oficiales fijados

| Tarea | Entrada, resultado y restricción del adaptador |
|---|---|
| MS MARCO Passage Ranking v1 | `MRR @10` con IDs y qrels del split original. Identificar búsqueda sobre colección completa, reranking de candidatos predefinidos o subconjunto. Registrar `QueriesRanked` y cobertura contra el conjunto esperado |
| BEIR, incluido SciFact y SCIDOCS en sus versiones BEIR | `NDCG@10` y `Recall@100`. Registrar las métricas adicionales que se soliciten. Conservar el valor de `ignore_identical_ids`, el universo de consultas y la cobertura evaluada. Pasar una copia del run si el evaluador elimina resultados in situ |
| BRIGHT | `NDCG@10` por tarea, con la colección y perfil elegidos. Aplicar `excluded_ids` por consulta antes de truncar el ranking final y verificar su ausencia. El evaluador fijado también calcula MAP, recall, precisión y MRR sin corte explícito |
| SciFact original | `abstract_label_only_f1`, `abstract_rationalized_f1`, `sentence_selection_f1` y `sentence_label_f1`. Fijar gold, sentencias y conjuntos de evidencia. Conservar precisión y recall si se usa modo verbose |
| SCIDOCS original | Macro‑F1 para `mag` y `mesh`. MAP y nDCG para `co-view`, `co-read`, `cite` y `co-cite`. `adj-NDCG` y `adj-P@1` para recomendación. Preservar splits, candidatos y ajustes del evaluador |
| arXiv, S2ORC, peS2o | Manifiesto de fuente documental o adaptación. Las métricas requieren una tarea y juicios explícitos. Añadir documentos al pool de un benchmark cambia su protocolo y debe registrarse como evaluación propia |

Revisiones consultadas y fijadas para reproducción. Usar estos commits como entradas del lock de cada evaluador.

- [MS MARCO, `ms_marco_eval.py`](https://github.com/microsoft/MSMARCO-Passage-Ranking/blob/28b369395881cdc50eb69173dcf2f94eb247395e/ms_marco_eval.py).
- [BEIR, evaluador](https://github.com/beir-cellar/beir/blob/ef83d29307061c65d04b035b4f4e7c18bd8374af/beir/retrieval/evaluation.py).
- [BRIGHT, protocolo de ejecución](https://github.com/xlang-ai/BRIGHT/blob/d99e8391d967d4c2b3a74732530d2309e2fc92b6/run.py) y [métricas](https://github.com/xlang-ai/BRIGHT/blob/d99e8391d967d4c2b3a74732530d2309e2fc92b6/retrievers.py).
- [SciFact, evaluador](https://github.com/allenai/scifact-evaluator/blob/66feffc5b2cc9e28e3ce3b8c9e824c3c642981eb/evaluator/eval.py).
- [SCIDOCS, entrada](https://github.com/allenai/scidocs/blob/ebf239d30d70062b4111f9e3a8efe2b3d3f3d303/scidocs/__init__.py), [clasificación](https://github.com/allenai/scidocs/blob/ebf239d30d70062b4111f9e3a8efe2b3d3f3d303/scidocs/classification.py), [citación y actividad](https://github.com/allenai/scidocs/blob/ebf239d30d70062b4111f9e3a8efe2b3d3f3d303/scidocs/user_activity_and_citations.py) y [recomendación](https://github.com/allenai/scidocs/blob/ebf239d30d70062b4111f9e3a8efe2b3d3f3d303/scidocs/recomm_click_eval.py).

### Artefactos que hacen interpretable cada comparación

| Capa | Qué cambiar | Qué congelar y guardar |
|---|---|---|
| Representación | Encoder, pooling o precisión | Corpus, fragmentos, consultas y regla de relevancia. Evaluar primero scoring exhaustivo bajo cada espacio. SCIDOCS original conserva sus tareas propias |
| Recuperación | Léxico, dense, sparse aprendido o late interaction | Qrels, split, corpus y unidad de documento. Guardar resultados por consulta |
| Error ANN | Búsqueda aproximada frente a exacta | Mismos vectores, métrica, filtros y snapshot. Comparar top‑K vecinos del conjunto elegible. Una prueba sobre subconjunto debe calcular ambos rankings sobre ese subconjunto |
| Fusión y ranking | Fusión, score o reranker | Preservar listas de entrada y candidatos. Aislar reranking sobre el mismo conjunto y repetir end-to-end para medir efectos de profundidad |
| Selección de contexto | Retención, orden, redundancia | Misma lista de candidatos y presupuesto. Conservar fragmentos admitidos, descartados y conjuntos de evidencia requeridos |
| Utilidad para el LLM | Contexto recuperado, control sin contexto y contexto de referencia cuando exista | Generador, plantilla, decodificación y preguntas. Evaluar corrección, soporte de citas y abstención por separado |
| Generalización | Colección/dominio | Configuración elegida antes del test. Informar idiomas y dominios por separado, solapamiento comprobable y límites sobre datos de entrenamiento desconocidos |
| Operación | Perfil de carga o fallo | Manifiesto, timestamps, estados de terminación, candidatos y cobertura. Informar latencias, errores, recursos y coste por etapa |

Cada run guarda el resultado oficial y un registro operacional separado. Una consulta con timeout no se trata como respuesta vacía válida. Si faltan consultas requeridas, el reporte señala la ejecución incompleta y conserva el denominador y los fallos, aunque el evaluador pueda calcular un número sobre el run parcial. No reutilizar test para seleccionar configuración.

Para el español, el fixture siguiente verifica integración y preservación de Unicode. Una afirmación de calidad en español necesita un conjunto de evaluación con juicios propios o un benchmark en ese idioma. Si se incorporan juicios propios, versionar consulta, unidad de relevancia, documento/revisión, etiqueta, criterio, procedencia y decisión de adjudicación. Mantener separados los estados de juzgado no relevante y no juzgado.

## 6. Testigos y oráculos independientes

Los datos de esta sección son sintéticos. Los hechos y permisos están definidos dentro del fixture. Las etiquetas de relevancia son manuales, independientes de embeddings y ranking. Cada ejecución de integración conserva el identificador de la historia, configuración, estado observado y diferencias respecto del esperado.

### Corpus y juicios testigo

```json witness
{
  "documents": {
    "es-protocolo": {"tenant": "lab", "revision": 1, "lang": "es", "text": "El protocolo LIMA conserva el registro durante siete días.", "vector": [0.8, 0.6]},
    "es-distractor": {"tenant": "lab", "revision": 1, "lang": "es", "text": "LIMA registra el nombre del dispositivo.", "vector": [0.6, 0.8]},
    "en-protocol": {"tenant": "lab", "revision": 1, "lang": "en", "text": "The LIMA protocol retains the log for seven days.", "vector": [0.0, 1.0]},
    "other-tenant": {"tenant": "other", "revision": 1, "lang": "es", "text": "El protocolo LIMA conserva el registro durante siete días.", "vector": [1.0, 0.0]}
  },
  "queries": {
    "retencion-es": {"text": "¿Cuánto tiempo conserva LIMA el registro?", "tenant": "lab", "vector": [1.0, 0.0], "qrels": {"es-protocolo": 1, "es-distractor": 0, "en-protocol": 1}, "answer": "siete días"},
    "retention-en": {"text": "How long does LIMA retain the log?", "tenant": "lab", "qrels": {"es-protocolo": 1, "es-distractor": 0, "en-protocol": 1}, "answer": "seven days"},
    "sin-evidencia": {"text": "¿Quién inventó LIMA?", "tenant": "lab", "qrels": {"es-protocolo": 0, "es-distractor": 0, "en-protocol": 0}, "answer": null}
  },
  "exact_top2": ["es-protocolo", "es-distractor"],
  "approximate_run_top2": ["es-protocolo", "en-protocol"],
  "expected": {"ann_recall_at_2": 0.5, "approximate_relevance_recall_at_2": 1.0}
}
```

Los vectores bidimensionales son entradas elegidas a mano para comprobar la búsqueda y su oráculo. La coincidencia entre el texto de dos tenants permite detectar un error de identidad o autorización aun cuando el hash del contenido sea igual. El documento de otro tenant está excluido por elegibilidad, independientemente de su score.

### Historias de aceptación

| Historia inyectada | Observación que debe producir la implementación | Oráculo y contraejemplo |
|---|---|---|
| Enviar dos veces la revisión 1 con la misma clave y payload | Un recibo y una revisión durable. Una intención de publicación registrada | Consultar registro y outbox. Cambiar el payload con la misma clave debe producir conflicto |
| Caer entre escritura del objeto y commit de PostgreSQL | Original recuperable para reconciliar. Ningún recibo durable para esa operación | Registro de commits y checksum. Detectar un cursor que se adelantó al commit |
| Caer después del commit, antes de responder o antes de consumir | El reintento devuelve el recibo original. El consumidor puede continuar | Repetir la misma clave y comparar el resultado tras reinicio |
| Terminar revisión 1 después del retiro 2 | El puntero permanece retirado. Resultado, contexto y caché respetan la inelegibilidad | Esperado explícito vacío para la identidad retirada. Buscar la resurrección incluso con proyección física presente |
| Conceder acceso, recuperar un candidato, revocar y luego revalidar | Se excluye el candidato de la respuesta y del contexto | Barreras controladas entre consulta, commit de ACL y revalidación |
| Consultar con un encoder distinto de igual dimensión | Rechazo por incompatibilidad de espacio antes de usar el índice | Dos manifiestos distintos, dimensión idéntica |
| Devolver resultados de reranker en otro orden | Cada score se asocia a su identidad original | Parejas con scores conocidos. Intercambiar dos IDs debe producir un fallo |
| Desconectar al cliente después del primer token | Estado terminal interno de desconexión y cancelación propagada | Backend instrumentado y medición de liberación de trabajo |
| Extraer una cita después de recortar su fragmento | La respuesta o su validador señala cita inválida | Resolución contra el manifiesto del prompt efectivo |
| Consultar un fixture válido con Vespa inaccesible | Error de dependencia, con cobertura fallida | Una respuesta de éxito sin resultados debe fallar la prueba |

### Oráculo ejecutable del fixture

El siguiente programa usa solo Python estándar. Recibe el JSON del bloque anterior como archivo. Es un oráculo aritmético para la colección finita y el filtro de tenant del testigo. Las historias de PostgreSQL, Vespa, ACL y serving requieren adaptadores de observación y dependencias reales.

```python oracle
import json
import math
import sys
from pathlib import Path

fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
docs = fixture["documents"]
query = fixture["queries"]["retencion-es"]


def check(condition, message):
    if not condition:
        raise ValueError(message)


def exact_cosine_top_k(documents, vector, allowed_ids, k):
    query_norm = math.hypot(*vector)
    check(query_norm > 0 and math.isfinite(query_norm), "invalid query norm")
    scored = []
    for doc_id in allowed_ids:
        other = documents[doc_id]["vector"]
        norm = math.hypot(*other)
        check(len(other) == len(vector), "dimension mismatch")
        check(norm > 0 and math.isfinite(norm), "invalid document norm")
        score = math.fsum(a * b for a, b in zip(vector, other)) / (query_norm * norm)
        scored.append((-score, doc_id))
    return [doc_id for _, doc_id in sorted(scored)[:k]]


def validated_ids(ranking, allowed_ids, k):
    found = set(ranking)
    check(len(ranking) <= k, "ranking exceeds k")
    check(len(found) == len(ranking), "duplicate result")
    check(found <= allowed_ids, "ineligible result")
    return found


eligible = {doc_id for doc_id, doc in docs.items() if doc["tenant"] == query["tenant"]}
exact = exact_cosine_top_k(docs, query["vector"], eligible, k=2)
check(exact == fixture["exact_top2"], "wrong exact order")
approx = validated_ids(fixture["approximate_run_top2"], eligible, k=2)
relevant = {doc_id for doc_id, label in query["qrels"].items() if label > 0}
check(relevant <= eligible, "fixture qrels include ineligible positives")
observed = {
    "ann_recall_at_2": len(approx & set(exact)) / len(exact),
    "approximate_relevance_recall_at_2": len(approx & relevant) / len(relevant),
}
check(observed == fixture["expected"], "metric mismatch")
for mutated in (["other-tenant", "es-protocolo"], ["es-protocolo", "es-protocolo"]):
    try:
        validated_ids(mutated, eligible, k=2)
    except ValueError:
        continue
    raise AssertionError(f"undetected invalid ranking: {mutated}")
print(json.dumps(observed, sort_keys=True))
```

En esta historia, el ranking aproximado pierde un vecino exacto y gana un documento juzgado relevante. Sus dos recalls tienen denominadores diferentes. Para las pruebas generales, definir empates, consultas sin elegibles y resultados con menos de K elementos. Verificar que el conjunto exacto comparte el mismo filtro y snapshot que el run aproximado.

Para ejecutar los bloques directamente desde este documento, guardar el Markdown y extraer los bloques etiquetados mediante el siguiente comando. Utiliza Python 3.12.14, la versión comprobada en el entorno de revisión.

```bash
python - <<'PY'
from pathlib import Path
import re
import subprocess
import sys
import tempfile

text = Path("CONTRATOS_Y_ENTORNO_REPRODUCIBLE_RETRIEVAL.md").read_text(encoding="utf-8")
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    for label, name in [("json witness", "witness.json"), ("python oracle", "oracle.py")]:
        match = re.search(r"^\x60{3}" + label + r"\n(.*?)^\x60{3}$", text, re.M | re.S)
        if match is None:
            raise ValueError(f"missing block {label}")
        (root / name).write_text(match.group(1), encoding="utf-8")
    subprocess.run([sys.executable, str(root / "oracle.py"), str(root / "witness.json")], check=True)
PY
```

Resultado esperado: `{"ann_recall_at_2": 0.5, "approximate_relevance_recall_at_2": 1.0}`.

## 7. Lock del entorno, datos y ejecuciones

### Perfiles de entorno y contenido que se fija

| Perfil | Contenido que se debe resolver y fijar | Evidencia mínima |
|---|---|---|
| Testigo puro | Python 3.12.14 para la ejecución registrada. Bloques fixture y oráculo preservados con el documento. Cero dependencias de terceros | Comando y salida del oráculo |
| Integración CPU | Plataforma y arquitectura, imágenes por digest de PostgreSQL, almacenamiento de objetos y Vespa, Python y lock transitivo, esquema, application package y migraciones | Arranque desde entorno limpio, versiones efectivas, comprobaciones de escritura, búsqueda y reinicio |
| Evaluadores | Un entorno aislado por conjunto compatible de dependencias. Commit del evaluador y lock de bibliotecas, incluidas `pytrec_eval` o dependencias antiguas cuando apliquen | Ejecución conocida con resultados esperados y manifest de entradas |
| Serving A100 | Imagen vLLM por digest, driver observado, runtime CUDA, pesos, tokenizer, template, dtype, cuantización y argumentos efectivos de scheduling | Inicio con el modelo real, solicitud de prueba, versión devuelta, VRAM y métricas |
| Datos | Revisión de dataset, URL, tamaño, SHA‑256 del artefacto, licencias registradas, splits, hashes de corpus/queries/qrels y manifiesto de transformaciones | Descarga e importación verificables y correspondencia de IDs |
| Runs | Código, hashes de datasets, modelo e índice, perfiles, profundidad, exclusiones, semilla, carga, plataforma, resultados crudos y observaciones de error | Run por consulta y métricas atribuibles a ese conjunto |

Una etiqueta de imagen, un rango de dependencias o el nombre de un modelo son datos de resolución. El lock final registra referencias inmutables y versiones transitivas. Aislar los evaluadores permite conservar protocolos con requisitos incompatibles entre sí. Antes de incorporar un evaluador, verificar sus dependencias y revisar el código que se ejecutará. El commit fija el código consultado, y el lock correspondiente fija el entorno de ejecución.

### Entregables del repositorio

| Artefacto | Contenido mínimo |
|---|---|
| Contratos versionados | Esquemas de los sobres, casos de error, transiciones y tablas de evidencia de este documento. Una prueba por frontera comprueba también los mensajes que emite el productor |
| Manifiesto de entorno por perfil | Plataforma, intérprete, imágenes por digest, lock transitivo y aplicación/configuración. Un perfil GPU añade driver observado y artefactos del modelo |
| Manifiesto de dataset | Fuente/revisión, hashes, tamaño, split, unidad de evaluación, lista de consultas esperadas y reglas de exclusión |
| Manifiesto de ejecución | Referencias inmutables a código, entorno, dataset, encoder, índice, perfil de ranking, evaluador y flags. Añadir generador y plantilla cuando corresponda |
| Evidencia | Run ordenado y estado por consulta, métricas del evaluador, comando reproducible, tiempos y recursos observados. Guardar reportes de error e información suficiente para explicar fallos |

El runner valida referencias y hashes antes de ejecutar. Una referencia pendiente impide declarar reproducible el perfil que la necesita. Comparar un cambio de código o modelo exige conservar el manifiesto anterior y el candidato, además de los resultados de ambos.

### Inventario previo en la máquina de destino

```bash
uname -srm
cat /etc/os-release
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader
free -b
df -B1 .
```

Estos comandos corresponden a un host Linux. Registrar su salida real y resolver versiones compatibles. Para cada imagen, conservar el digest correspondiente a la plataforma, más los argumentos efectivos. Para cada modelo, guardar revisión y hashes de pesos/configuración/tokenizer. Resolver Python y bibliotecas por perfil y verificar instalación desde el lock en un entorno limpio. La disponibilidad de artefactos externos debe comprobarse al materializar cada lock.

### Capacidad de datos

Tratar los 190 GB como un límite de planificación hasta medir los bytes libres. Antes de descargar o reconstruir, contabilizar en el pico de uso:

```text
archivo de descarga + extracción + originales retenidos
+ texto y fragmentos + vectores + índice y metadatos
+ generaciones activas/candidatas + cachés de modelos
+ espacio temporal de build + reservas de recuperación
```

La colección MS MARCO Passage Ranking v1 documenta 8,841,823 pasajes. Con un vector de 768 dimensiones float32 por pasaje, solo las celdas de vectores sumarían 27,162,080,256 bytes, aproximadamente 25.3 GiB. El cálculo excluye grafo, texto, metadatos y memoria del proceso. Probar con un subconjunto identificado permite medir la huella por elemento antes de planificar la colección completa. Mantener los resultados de esa prueba separados de los resultados de colección oficial completa. [Colección original de Microsoft](https://github.com/microsoft/MSMARCO-Passage-Ranking/blob/28b369395881cdc50eb69173dcf2f94eb247395e/README.md).

Antes de reconstruir, medir si caben simultáneamente índice activo y candidato. Para arXiv, S2ORC y peS2o, registrar exactamente los documentos o shards seleccionados, su procedencia y el motivo de exclusión. Las 40 GB de VRAM se presupuestan aparte entre pesos, KV, activaciones, workspace y procesos concurrentes.

## 8. Criterios de cierre y evidencia

| Comprobación | Cierre verificable | Estado de esta entrega |
|---|---|---|
| Especificación por frontera | Campos, precondiciones, postcondiciones, fallos y dueño del estado identificados | Propuesta documentada |
| Fixture y oráculo | Resultado exacto reproducido y detección de mutaciones de tenant y duplicación | Ejecutado con Python 3.12.14 |
| Revisiones de evaluadores | Commit fijo y archivo correspondiente comprobados | Inspeccionados en GitHub. Ejecución oficial pendiente |
| Integración durable | Historias de crash, reentrega, orden, retiro y autorización observadas con servicios reales | NO EJECUTADO |
| Entorno de destino | Inventario real, locks transitivos, digests y smoke tests CPU/GPU registrados | Pendiente de la máquina de destino y de materializar artefactos |
| Benchmarks | Datasets, splits, exclusiones, métricas y cobertura por consulta reproducibles | NO EJECUTADO |

La primera integración que cierra el núcleo debe producir un recibo durable, publicar una revisión consultable en Vespa y retirarla sin que un trabajo atrasado pueda devolverla. El testigo bilingüe y sus juicios se reutilizan después para conectar retrieval, contexto y vLLM, conservando evidencia de cada frontera.

