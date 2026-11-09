# Retención del laboratorio bibliográfico

| Artefacto | Responsable | Condición de conservación y eliminación | Evidencia de cierre |
| --- | --- | --- | --- |
| Corpus público, consultas, qrels, rankings y locks | Operador del laboratorio | Conservar mientras respalden una evaluación o versión mantenida. Documentar licencia y procedencia. | Reconstrucción del manifiesto e integridad de archivos. |
| Consulta privada, prompt y respuesta | Operador del servicio | Vida de la solicitud. Liberar al completar, cancelar o expirar. Logs ordinarios limitados a metadatos. | Pendiente inspección del transporte y buffers reales. |
| Trazas operativas | Operador del laboratorio | Conservar con la corrida mientras respalde evidencia. Capturas con contenido requieren propósito, acceso y expiración explícitos. | Auditoría de artefactos y campos registrados. |
| Índice reemplazado | Operador de despliegue | Conservar mientras la versión anterior esté declarada elegible para rollback y existan lectores. Retirar al cerrar ambas referencias. | Pendiente ensayo de drenaje, retirada física y reconciliación. |
| Tombstones, ACL y registro de aceptación | Operador de la autoridad | Conservar mientras cualquier replay, backup o versión mantenida pueda referenciar esas revisiones. | Pendiente restauración y prueba de exclusión antes de habilitar consulta. |

Esta política fija condiciones del laboratorio. Las ventanas temporales y los backups del despliegue de destino se adoptan con su perfil. Los resultados de prueba deben acompañar cada regla antes de declararla operativa.
