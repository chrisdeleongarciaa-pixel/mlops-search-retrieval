# Matriz de verificación de aceptación durable

**Contrato** · [Aceptación durable de revisiones v1](../contracts/accept-revision.md)  
**Estado** · Verificado en el perfil PostgreSQL y almacén de archivos.

## Oráculos y niveles

Para las pruebas de contrato, el oráculo puede ser una tabla de historias calculadas a mano o un modelo de estado independiente. Por documento, el modelo conserva las aceptaciones por posición, el contador interno y la posición deseada. Calcula el resultado a partir del orden de las operaciones observadas y conserva el primer recibo de cada clave. Las pruebas calculan los digests de fixtures con una función estándar independiente de la lógica del caso de uso.

Para integración, una conexión de prueba independiente lee el registro de revisiones, la revisión deseada y la outbox en PostgreSQL. El cliente de prueba obtiene el original directamente del almacén y calcula su digest. El estado esperado se especifica antes de la ejecución. Las comprobaciones se limitan a efectos contractuales, como la relación entre revisión y evento, y permiten cambiar la estructura interna de las tablas.

## Casos y trazabilidad

| Caso | Cláusulas | Nivel | Estímulo | Resultado esperado |
| --- | --- | --- | --- | --- |
| AV-01 | [AC-01](../contracts/accept-revision.md#ac-01--entrada-y-precondición), AC-05 | Contrato de frontera | Enviar versión no soportada, identificador vacío, posición booleana o fuera de rango, retiro con original y original mayor que el límite. Repetir con Python optimizado cuando exista la frontera. | Rechazo explícito sin aceptación ni evento. Una identidad sin acceso se rechaza antes de realizar efectos en el ámbito ajeno. |
| AV-02 | AC-01, AC-03, AC-05 | Contrato e integración | Enviar bytes distintos de los que describe el digest declarado. En otro ensayo, hacer que la verificación del objeto almacenado detecte un digest distinto. | `INTEGRITY_ERROR`. El registro durable y la outbox permanecen intactos. |
| AV-03 | AC-03, AC-04 | Integración | Aceptar un original válido para un documento nuevo. | Un objeto con los bytes y digest esperados, una revisión con secuencia 1, una revisión deseada y un evento ligado a la revisión. El recibo coincide con los campos persistidos. |
| AV-04 | AC-02, AC-03, AC-04, AC-05 | Integración | Cortar el proceso después del commit y antes de responder. Reiniciar y reenviar el mismo comando. | La aceptación ya existe antes del reintento. El reintento devuelve el recibo persistido, con la misma secuencia, identidad de evento e instante de aceptación. |
| AV-05 | AC-03, AC-05 | Integración | Tras verificar el objeto, forzar rollback al intentar registrar el evento después de la revisión. | La revisión, el estado deseado y la outbox conservan el estado previo. El objeto queda sin referencia si era nuevo. El reintento válido produce una sola aceptación. |
| AV-06 | AC-02, AC-04, AC-05 | Contrato e integración | Reutilizar una posición aceptada cambiando los bytes con un digest válido, la operación o la etiqueta de revisión de origen. | `IDEMPOTENCY_CONFLICT`. El recibo persistido, la revisión deseada y el evento original conservan sus valores. |
| AV-07 | AC-02, AC-04 | Contrato e integración | Aceptar posiciones 7 y 9 con etiquetas opacas `Z` y `A`. Reenviar la 7 y enviar por primera vez la 8. | La 7 devuelve el recibo con secuencia 1. La 8 devuelve `STALE_REVISION`. La 9 sigue deseada con secuencia 2. Hay dos eventos lógicos. |
| AV-08 | AC-02, AC-04 | Integración concurrente | Sincronizar dos solicitudes para la misma primera posición de un documento. Repetir con comandos equivalentes y con comandos distintos. | En el caso equivalente, ambas devuelven el mismo recibo. En el caso distinto, la aceptación ganadora conserva su recibo y la otra recibe conflicto. En ambos casos quedan una revisión y un evento. |
| AV-09 | AC-04 | Integración concurrente | Coordinar los dos órdenes posibles de commit o decisión durable para posiciones 7 y 8. | Si la 7 se acepta primero, hay dos revisiones y dos eventos. Si la 8 se acepta primero, la 7 queda obsoleta y hay una sola revisión y un solo evento. En ambos casos la 8 queda deseada. |
| AV-10 | AC-03, AC-04 | Contrato e integración | Aceptar `UPSERT` en posición 7 y `WITHDRAW` en posición 8. Reenviar la 7. | El retiro tiene secuencia 2 y su evento carece de original asociado. El reintento devuelve el recibo histórico del `UPSERT` y el retiro sigue deseado. |
| AV-11 | AC-01, AC-02, AC-04 | Contrato e integración | Enviar la misma posición e identificador documental bajo dos fuentes del mismo tenant y dos tenants autorizados. | Cada identidad tiene su registro, primera secuencia, revisión deseada y evento. Un conector de un ámbito carece de acceso al registro del otro. |
| AV-12 | AC-05 | Integración | Perder la confirmación del commit con una conexión interrumpida. Ejercitar una transacción que confirmó y una que se revirtió. | Si el resultado sigue incierto, se informa `OUTCOME_UNKNOWN`. Al reenviar y estabilizar el estado, hay una sola aceptación y un solo evento. En la historia con commit se conserva el recibo previo. |
| AV-13 | AC-03, AC-05 | Integración | Interrumpir el almacén antes de verificar el objeto. Interrumpir el proceso en otro ensayo tras verificar el objeto y antes de iniciar la transacción. | PostgreSQL conserva el estado inicial. El objeto del segundo ensayo puede reconciliarse como huérfano. El reintento produce una sola aceptación. |

Las cláusulas AC-02 a AC-05 están en el [contrato v1](../contracts/accept-revision.md). Al implementar cada caso, identifica la prueba con su ID AV y registra sus cláusulas AC en la suite o en su manifiesto. Así se puede navegar del requisito a la prueba y de la prueba al requisito.

## Primer ensayo de recuperación · AV-04

1. Arranca una instancia de ingesta aislada con PostgreSQL y el almacén persistentes. Deja el consumidor de outbox detenido para conservar el evento en observación. Usa un documento sintético nuevo y un comando de posición 7 con digest conocido.
2. Coloca una barrera del arnés después de que el adaptador confirme el commit y antes de que el recibo se entregue al llamador. Desde una conexión independiente, observa una revisión con secuencia 1, esa revisión como deseada y un solo evento asociado. Captura los campos del recibo a partir del estado confirmado.
3. Mientras la barrera sigue cerrada, termina el proceso. Comprueba desde el cliente que no recibió un recibo. Reinicia la ingesta contra los mismos servicios, sin restaurar ni borrar su estado.
4. Antes de reenviar, vuelve a leer el registro y el original. Confirma que los datos persistidos coinciden con la primera observación. Este paso distingue la supervivencia de la aceptación de una aceptación nueva durante el reintento.
5. Reenvía exactamente el mismo comando. Compara todos los campos del recibo con los registrados en el paso 2. Vuelve a comprobar una sola revisión, una sola identidad de evento y la misma revisión deseada.

La barrera debe sincronizar el corte mediante una señal observable del arnés. Registra las versiones de servicios, la configuración relevante, el digest del fixture, el punto del corte, las observaciones antes y después del reinicio y el resultado. Una prueba con un doble puede comprobar la lógica del reintento. El ensayo AV-04 requiere PostgreSQL y el almacén reales.

## Evidencia de ejecución

La ejecución usó Python 3.12.10 en Windows, Psycopg 3.3.8, PostgreSQL 17.11 en un contenedor local y `FileObjectStore` en un directorio persistente entre procesos. Pasaron 4 pruebas unitarias, 8 de contrato y 12 de integración. Las 8 de contrato también pasaron con `python -O`. Las pruebas se identifican por sus cláusulas AC y casos AV en [contrato](../../tests/contract/test_accept_revision.py) e [integración](../../tests/integration/test_accept_revision_postgres.py).

En AV-04, el original de prueba tenía SHA-256 `37444d9a4356fefabdd2fb8cb2598bb713b38a7965f6ebbc8f640cef80a6ce2a`. La conexión independiente observó una revisión de posición 7 y secuencia 1, deseada y con un evento. El proceso terminó en la barrera sin entregar el recibo. Antes del reenvío, la misma revisión, evento y original seguían disponibles. Un proceso nuevo devolvió los campos del recibo persistido, incluidos el identificador del evento y el instante de aceptación. La observación final seguía mostrando una revisión y un evento.

AV-12 inyectó la pérdida de confirmación en el límite del driver tras dos resultados reales de PostgreSQL, commit y rollback. Las dos historias se reconciliaron con un reintento. AV-08 y AV-09 usaron conexiones concurrentes al motor, y la prueba de historias generadas comparó 45 operaciones con un modelo independiente de aceptación.

## Validación posterior

La matriz verifica el contrato con datos sintéticos. Para validar el uso previsto, ejecuta un replay representativo de un conector real y comprueba que puede conservar posiciones estables, reconciliar reintentos y volver a avanzar tras un reinicio. La evaluación de relevancia y entrega se realiza al incorporar sus respectivos contratos.
