# Verificación de los incrementos testigo

Los IDs de esta matriz remiten al [contrato ejecutable](../contracts/witness-increments.md). La verificación contractual y los ensayos de carga de destino tienen oráculos distintos.

| ID | Prueba reproducible | Oráculo |
| --- | --- | --- |
| SE-01 | Migración sobre historial v1, evento nuevo, reentrega, reuso de ID y evento obsoleto. | Recibo v1 idéntico, conflicto bajo el mismo ID y conteo de outbox esperado en PostgreSQL. |
| SE-02 | Entrega 1, 3 y reanudación de la entrega íntegra, seguida de una variante conflictiva. | Checkpoint 1 antes de llenar el hueco y checkpoint 4 tras reanudar. El conflicto deja su feed detenido en 1. |
| SE-03 | Cortes de proceso en el commit y reenvío desde un proceso nuevo. | Conexión independiente, checksum del original y comparación de recibos con PostgreSQL. |
| WP-01 | Publicar, mantener continuidad entre actualizaciones, alimentar más proyecciones obsoletas que el límite, retirar con un worker atrasado y volver a dar de alta. | Vespa y PostgreSQL reales. La proyección física atrasada queda excluida de búsqueda y contexto. |
| WP-02 | La misma historia con un índice controlado y una concesión y revocación de ACL en PostgreSQL. | Estado SQL observado, candidatos y contexto esperados. |
| LX-01 | Tras WP-01, medir el testigo léxico sobre una generación estable y registrar candidatos y tiempos al motor y de recuperación. | [Sondeos y protocolo](../operations/witness-lab.md#wp-01-y-referencia-léxica-lx-01). Ensayo de captura con doble ejecutado. Medición con Vespa pendiente. |
| RB-01 | Corpus sintético, exacto filtrado y fusión de dos listas. | Cálculo independiente del fixture y ejemplo RRF calculado a mano. |
| DL-01 a DL-07 | Revocación antes del contexto y durante generación, autoridad caída, truncamiento de evidencia, desconexión, confirmación pública y saturación. | Backend instrumentado y contenido o estado terminal observado. |

## Ejecución

Desde la raíz del proyecto, instala las dependencias y exporta `ACCEPT_REVISION_TEST_DSN` para una base desechable. La suite crea y elimina únicamente esquemas de prueba con nombres generados. Para WP-01, exporta también `WITNESS_VESPA_URL` hacia una instancia de laboratorio con el paquete de `infra/vespa/app`.

```powershell
python -B -m unittest discover -s tests/unit -v
python -B -O -m unittest discover -s tests/contract -v
python -B -m unittest discover -s tests/integration -v
python -B evaluation/fixtures/witness_oracle.py evaluation/fixtures/witness.json
```

Para el modelo acotado, ejecuta TLC con `verification/tla/WitnessPublication.cfg` y `WitnessPublication.tla` desde un directorio de trabajo. El modelo explora aceptación, retiro, trabajo atrasado, alimentación y crash con cuatro revisiones. Los ensayos de conectividad, WAL, ACL y visibilidad corresponden a las suites del sistema.

## Evidencia observada

- **Ingesta.** Pasaron las 12 pruebas de regresión v1 y los casos SE-01, SE-02 y SE-03 con Python 3.12.10, Psycopg 3.3.8, PostgreSQL 17.11 y originales persistentes entre procesos. El corte previo al commit dejó el registro de eventos vacío. El corte posterior dejó una aceptación que el proceso nuevo pudo recuperar con el mismo ID y timestamp. La entrega con hueco dejó el cursor en 1 y el reenvío completo lo llevó a 4.
- **Publicación.** WP-02 pasó con PostgreSQL y un índice controlado. La secuencia deseada impidió activar la proyección tardía, el retiro siguió excluido al consultar un resultado guardado y una actualización posterior permaneció excluida hasta publicar. El manifiesto de proyección quedó persistido y se comprobó la concesión y revocación SQL. TLC 2.19 exploró 7.121 estados generados, 1.716 distintos y profundidad 18, sin violaciones del invariante del modelo.
- **Evaluación.** El oráculo del corpus reprodujo recall ANN@2 de 0,5 y recall de relevancia@2 de 1,0. SciFact BEIR obtuvo NDCG@10 de 0,66168 y Recall@100 de 0,88589 con BM25 sobre 5.183 documentos y 300 de 300 consultas del split test. Los valores corresponden al [baseline léxico y su manifiesto](../../evaluation/evidence/scifact-bm25-v1/manifest.json).
- **Entrega.** Pasaron 7 pruebas unitarias y 15 de contrato, estas últimas con `python -O`. Los ensayos de entrega usaron un backend instrumentado. La consulta de inventario GPU en Docker devolvió que WSL carecía de adaptadores visibles.

WP-01 sigue pendiente. Después de los timeouts iniciales de descarga, se descargaron directamente el manifiesto y las capas de Vespa 8.781.11 por el digest fijado en el perfil CPU. Se verificaron los SHA-256 antes de importar y se comprobaron la configuración y las cinco identidades de capa después de cargar la imagen en Docker como `vespa-wp01-verified:8.781.11`.

PostgreSQL 17.11 arrancó y confirmó `fsync`, `synchronous_commit` y `full_page_writes` activos. Docker expuso aproximadamente 3,8 GiB a Linux y el arranque normal de Vespa terminó con código 2 por su comprobación de memoria. El intento aislado con `VESPA_IGNORE_NOT_ENOUGH_MEMORY=true`, límite de 3.500 MiB y heap de config server de 512 MiB inició JDisc y creó el tenant, pero el config server registró timeouts de ZooKeeper y agotó el plazo de disponibilidad. El paquete de aplicación quedó sin desplegar en este intento, por lo que todavía falta ejecutar la prueba funcional contra Vespa real. [Requisitos de memoria de Vespa](https://docs.vespa.ai/en/operations/self-managed/docker-containers.html#memory)

En el runtime administrado de Colab del 6 de noviembre de 2026, el inventario observó Ubuntu 24.04.4 x86_64, Python 3.13.16, 12 CPU en la máscara de afinidad, 52,96 GiB de RAM y 112,54 GiB de disco libre antes de instalar dependencias. Se verificaron el bind en loopback y la inicialización de Podman 4.9.3 con crun y VFS. La imagen de PostgreSQL fijada en el perfil CPU se descargó. Al ejecutar `postgres --version`, crun falló con `ioctl(SIOCSIFFLAGS): Operation not permitted` con `--network=none`, y con `mount proc: Permission denied` en la comprobación con `--network=host`. El ensayo se detuvo en la verificación de contenedores. WP-01 y LX-01 siguen pendientes de un host que ejecute el perfil.

La GPU asignada por ese runtime fue una NVIDIA L4 de 23.034 MiB, con driver 580.82.07 y CUDA 13.0 anunciada por `nvidia-smi`. El selector del notebook indicaba A100 mientras la sesión conectada seguía en L4. No se instaló vLLM ni se midió inferencia en ese runtime.

En un runtime CPU de Colab separado se reprodujo el error `RTNETLINK answers: Operation not permitted` al activar `lo` en un namespace de red creado como root. Al crear primero un namespace de usuario con `unshare --user --map-root-user --net`, se habilitó `lo` y se verificó una transferencia TCP de cuatro bytes sobre `127.0.0.1` dentro del namespace. El montaje de `/proc` con un namespace de PID nuevo fue denegado tanto con el usuario mapeado como con root. El dispositivo `/dev/net/tun` no estaba presente. El ensayo separó el permiso de red del bloqueo del montaje que impide usar el perfil OCI aislado probado. [Notebook de diagnóstico](https://colab.research.google.com/drive/1Rvf4XUKjoGuOEezGmkXNgiUm57ueZZRc)

Como control, [la sonda de namespaces](../../infra/vespa/check-container-namespaces.sh) pasó ambas comprobaciones en Debian WSL2. Docker Desktop arrancó un PostgreSQL local 17.11 desechable, publicó su puerto en `127.0.0.1` y aceptó una conexión TCP desde Windows. La consulta `SELECT version()` y el arranque se observaron con la imagen local de digest `sha256:748a18a54d3842d43bc156f0840d78029b8d349e0feca98c12b9c4ce3404a6b3`. El contenedor se eliminó tras el ensayo. Este control cubre la red local de Docker. El motor seguía reportando 3,82 GiB de RAM y WP-01 no se ejecutó.

## Reintento local de WP-01 con arranque por etapas

Se inició el servidor de configuración de Vespa 8.781.11 con heap máximo de 384 MiB, después PostgreSQL 17.11 con sus tres controles de WAL activos y finalmente el proxy y el nodo de datos. La aplicación testigo se activó y el endpoint de búsqueda llegó a reportar cobertura completa. WP-01 se ejecutó contra ambos servicios. Falló en el primer `POST /document/v1/lab/fragment/docid/...` porque la conexión se cerró antes de recibir una respuesta. El ensayo se detuvo antes de comprobar la retirada.

En un reintento, una copia temporal del paquete fijó el heap del proceso de búsqueda en 512 MiB, con tres CPU asignados al contenedor. La aplicación se activó. El servidor de datos inicializó el nodo de almacenamiento, pero la sonda no consiguió confirmar una escritura y su lectura dentro del plazo de 180 s. El servidor de configuración registró un timeout de lectura al consultar las métricas de ZooKeeper. La lectura de `memory.events` mostró `oom=0` y `oom_kill=0`, por lo que el último fallo queda registrado como un problema de disponibilidad del servicio con causa aún abierta. WP-01 y LX-01 siguen pendientes de un entorno que sirva ambas rutas de Vespa de forma estable.

## Piloto de inferencia BF16 en la L4 de Colab

El 6 de noviembre de 2026 se ejecutó Qwen2.5-7B-Instruct, revisión `a09a35458c702b33eeacc393d103063234e8bc28`, con vLLM 0.33.0, PyTorch 2.14.0+cu130, Transformers 5.20.2, Python 3.13.16 y driver NVIDIA 580.82.07. Se verificó el SHA-256 de los 11 archivos del [manifiesto](../../config/model-sources.json). La [configuración del piloto](../../infra/serving/vllm/README.md#ensayo-de-capacidad-en-colab-l4) conserva el presupuesto de VRAM, el contexto máximo y la forma de la carga. El primer arranque falló durante el calentamiento de FlashInfer porque faltaba Ninja. El arranque con el muestreador nativo pasó `/health` y devolvió `4` en la prueba de chat.

| Concurrencia | Tokens de entrada y salida por solicitud | Completadas | Tokens de salida/s | TTFT p95 (ms) | ITL p95 (ms) | E2E p95 (ms) |
| ---: | --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 1.024 + 96 | 16/16 | 16,91 | 276,00 | 57,37 | 5.692,73 |
| 4 | 1.024 + 96 | 16/16 | 57,00 | 1.097,81 | 59,65 | 6.823,64 |
| 8 | 1.024 + 96 | 16/16 | 97,07 | 2.168,63 | 60,97 | 8.399,80 |
| 8 | 3.800 + 256 | 8/8 | 81,63 | 8.215,01 | 66,44 | 25.066,45 |

La caché KV reportó 44.240 tokens. De 564 muestras de GPU tomadas cada 500 ms, la menor memoria libre fue 4.902 MiB de 23.034 MiB (21,28 %). Tras apagar el servidor, se observaron de nuevo 22.564 MiB libres. La carga completó 56 solicitudes sin errores. El TTFT p95 quedó dentro de 1,5 s con una y cuatro solicitudes concurrentes. La latencia total p95 superó 3 s en los cuatro puntos, antes de integrar recuperación y gateway. La muestra sirve como piloto sintético de la L4. La validación del objetivo de error, la cancelación de una solicitud activa y la carga representativa siguen pendientes.

Los logs, el entorno resuelto, el perfil ejecutado y los resultados por solicitud se empaquetaron en `/content/retrieval-l4-evidence.zip` (112.025 bytes). La descarga desde el navegador agotó el plazo. El ZIP quedó en el runtime y este resumen quedó guardado en el repositorio. [Notebook de Colab](https://colab.research.google.com/drive/1vkfT1kZGJ2WLb86iAyvSrJwhI93yMjs0)

La comparación densa y ANN con E5, los demás benchmarks, la calidad representativa en español y la inferencia con carga en la A100 requieren sus respectivos artefactos y ensayos.
