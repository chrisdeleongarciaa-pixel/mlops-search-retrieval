# 0001 · Continuidad entre revisiones

**Estado** · Pendiente.

Si la revisión nueva sobrescribe la anterior en Vespa antes de activarse en PostgreSQL, una consulta puede quedarse sin ninguna revisión elegible. Si coexisten y la elegibilidad se aplica después de truncar candidatos, las revisiones obsoletas pueden ocupar toda la lista.

La decisión debe fijar la identidad de cada proyección, el punto de activación, la selección del conjunto elegible antes del límite de candidatos y la recolección de revisiones antiguas. Si se usa escritura condicional, hay que verificar cómo se comportan las operaciones de creación, los reintentos y la ausencia de réplicas.

**Evidencia para cerrar.** Una consulta testigo sigue encontrando la revisión 7 mientras la 8 se prepara y pasa a la 8 cuando se publica. Un retiro durante el procesamiento sigue siendo efectivo después de que termina un trabajo retrasado. Con suficientes revisiones obsoletas para llenar una lista de candidatos, la revisión elegible sigue llegando a recuperación.
