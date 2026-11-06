# Almacenamiento de objetos

El primer perfil usa `FileObjectStore` con un directorio privado, existente y persistente. El sistema de archivos debe admitir enlaces duros dentro del directorio. Configura el mismo directorio al reiniciar el proceso o al conectar otros procesos que compartan el almacén.

El adaptador escribe y sincroniza un archivo temporal, comprueba sus bytes y publica el objeto con un enlace que preserva cualquier referencia existente. Al reutilizar una referencia, comprueba el tamaño y SHA-256 del archivo. En POSIX también sincroniza el directorio. La prueba en Windows cubre la terminación y el reinicio del proceso con el mismo directorio persistente.

El directorio debe estar protegido frente a escrituras ajenas al adaptador. La limpieza de objetos y archivos temporales requiere coordinarse con las aceptaciones en curso y verificar las referencias vigentes. El perfil inicial conserva esos archivos hasta que se defina esa política.
