# Serving con vLLM

Aquí se incorporará la configuración para cargar el modelo, reservar recursos, limitar concurrencia y verificar disponibilidad del backend. Registra las revisiones del modelo, tokenizer y plantilla, junto con la versión del backend y los datos del entorno GPU en el manifiesto.

El gateway aplica los presupuestos y la política de entrega. Las pruebas de integración deben observar la cancelación y la liberación de recursos en el proceso de inferencia.

## Inventario en Colab

Ejecuta `colab_preflight.py` en el runtime Linux de Colab con `/content` y conserva el JSON junto al run. También se puede pegar el archivo como una celda de Python. La sonda registra CPU, RAM, disco, GPU, driver y las versiones instaladas que detecta. Reporta por separado la memoria y CPU visibles y los límites de cgroup v2 cuando están disponibles. La presencia del CLI de Docker por sí sola es una observación del inventario.

La sonda consulta el runtime sin instalar paquetes, descargar pesos ni escribir archivos. Un fallo de `nvidia-smi` aparece como un error en el JSON. Antes de materializar el modelo, contrasta el espacio libre con el tamaño de los pesos, la caché y el entorno de vLLM.

## Ensayo de capacidad en Colab L4

Usa el modelo Qwen del [manifiesto](../../../config/model-sources.json) y comprueba el SHA-256 de todos los archivos antes de arrancar. El piloto usa vLLM 0.33.0 con el backend CUDA 13.0 en un entorno de Python aislado. Crea el entorno con `python -m venv --without-pip venv` y usa `uv pip install --python venv/bin/python --torch-backend=cu130 --only-binary=:all: vllm==0.33.0`. Guarda `uv pip freeze --python venv/bin/python` junto con el ensayo.

El servidor escucha en `127.0.0.1`, carga los pesos en BF16 y admite hasta 4.096 tokens por solicitud y ocho secuencias simultáneas. Fija `--gpu-memory-utilization 0.77`, `--max-num-batched-tokens 2048`, `--enable-chunked-prefill`, `--no-enable-prefix-caching`, `--enforce-eager` y `--generation-config vllm`. El muestreador nativo se activa con `VLLM_USE_FLASHINFER_SAMPLER=0` para evitar el fallo de arranque por Ninja durante el calentamiento del muestreador FlashInfer. [Variables de vLLM](https://docs.vllm.ai/en/latest/configuration/env_vars/)

Antes de medir, comprueba `/health` y una respuesta de chat con resultado conocido. Para el recorrido de completions, usa `vllm bench serve`, datos aleatorios con semilla 47, una solicitud de calentamiento, `--ignore-eos`, `--request-rate inf` y `--random-range-ratio 0`. Mide 16 solicitudes de 1.024 tokens de entrada y 96 de salida en cada nivel de concurrencia 1, 4 y 8. La carga cercana al límite usa ocho solicitudes concurrentes de 3.800 tokens de entrada y 256 de salida. Conserva las salidas por solicitud y los percentiles 50 y 95 de TTFT, TPOT, ITL y E2E. Muestrea VRAM con `nvidia-smi` cada 500 ms desde el arranque hasta que termine el ensayo. [CLI de benchmark](https://docs.vllm.ai/en/latest/cli/bench/serve/)
