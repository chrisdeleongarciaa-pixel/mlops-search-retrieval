# Contribuir

- Instala el paquete en un entorno virtual y ejecuta las pruebas unitarias descritas en el README antes de abrir un cambio.
- Coloca cada interfaz junto al caso de uso que la consume. Pon una definición en `contracts` cuando represente una identidad o un contrato compartido por varios componentes.
- Conecta los adaptadores en el punto de arranque de cada proceso. Mantén los clientes externos y sus credenciales fuera de los casos de uso.
- Cambia una decisión registrada cuando cambie una garantía. Describe el escenario adverso, cómo observar el resultado y dónde queda la evidencia.
- Usa fixtures sintéticos en el repositorio. Mantén credenciales, modelos, corpus reales y resultados de ejecución fuera del control de versiones.
- Ejecuta las pruebas contra motores reales al cambiar el comportamiento de una integración. Registra las versiones probadas y conserva resultados reproducibles.
