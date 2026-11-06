# Adaptadores

El código de frontera se agrupa por integración cuando se implementa. PostgreSQL conserva el registro de revisiones, elegibilidad y outbox. Vespa mantiene proyecciones de búsqueda. El almacén de objetos conserva originales y artefactos inmutables. vLLM ejecuta inferencia. Los adaptadores de entrada exponen casos de uso al gateway y al worker.

Cada adaptador traduce tipos y errores en su frontera. La configuración de red, credenciales y clientes se inyecta desde el arranque del proceso. Los archivos operativos de los servicios residen en [infra](../../../infra/README.md).
