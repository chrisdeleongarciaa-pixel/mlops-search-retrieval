# Vespa

Aquí se incorporará el paquete de aplicación con esquema, campos, perfiles y configuración de servicios. Mantén un vínculo entre la versión de esquema, la generación del índice y el espacio vectorial del manifiesto de versión.

La [publicación](../../docs/decisions/0001-publicacion.md) determina la identidad de las proyecciones y su elegibilidad. El [baseline RRF](../../docs/decisions/0004-rrf.md) determina los perfiles y la evidencia que deben conservar las consultas experimentales.

## Verificar namespaces en un runtime administrado

En un runtime Linux con `unshare`, `ip`, `timeout` y Python 3, ejecuta `bash infra/vespa/check-container-namespaces.sh`. La sonda comprueba por separado la activación y escucha de loopback dentro de un namespace de red nuevo y el montaje de `/proc` dentro de un namespace de PID nuevo. El código 0 indica que pasaron ambas comprobaciones. El código 1 identifica al menos un fallo y el 2 indica que falta una herramienta. La prueba crea namespaces transitorios y no abre puertos fuera de ellos. Después de pasarla, verifica el motor OCI con la imagen fijada antes de arrancar Vespa.
