# Pruebas de integración

Usa instancias aisladas y datos sintéticos. Registra las versiones de los servicios y comprueba efectos observables en PostgreSQL, Vespa y el almacén de objetos. Cuando se conecte vLLM, distingue la integración de protocolo con un doble de la ejecución real con modelo y GPU.

La primera prueba de ingesta es [AV-04](../../docs/verification/accept-revision.md#primer-ensayo-de-recuperación--av-04). Corta el proceso entre el commit de la aceptación y la entrega del recibo, y comprueba la recuperación al reiniciar y reenviar.

## Ejecutar la suite de aceptación

Instala el paquete con el extra `postgres` y proporciona `ACCEPT_REVISION_TEST_DSN` para una base PostgreSQL desechable. El rol debe poder crear y eliminar los esquemas de prueba generados por la suite. Cada caso aplica la migración en su propio esquema y usa un directorio temporal para originales. Ejecuta desde la raíz del proyecto con el Python del entorno virtual.

```powershell
python -m pip install -e ".[postgres]"
python -m unittest discover -s tests/integration -v
```

Si falta el DSN, la suite de PostgreSQL se omite. Para AV-04 y AV-13, el entorno debe permitir iniciar y terminar procesos hijo.

## Publicación

El primer recorrido que incluya publicación debe aceptar un cambio, comprobar que su revisión se hace consultable y resolver un fragmento a su original. Después añade una actualización con el feed retrasado, un retiro durante el procesamiento, una revocación durante la consulta y una cancelación durante la entrega.
