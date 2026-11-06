# Configuración

`release.example.toml` es la plantilla de un conjunto compatible. Los valores vacíos indican referencias por resolver. Para registrar una versión candidata, completa los campos con revisiones inmutables o identificadores de artefactos verificables, y vincula su evidencia de evaluación. Los parámetros propios de cada entorno se colocan en `config/local/`, excluido de Git. Gestiona los secretos a través del mecanismo del entorno.

El manifiesto documenta la composición de una versión. Su lectura y validación para activar servicios forman parte de la integración futura.
