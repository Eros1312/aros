# Instalación y operación

## Demo local comprobable

Usa Python 3.12, Node.js 24 y pnpm 11.25.0. En la raíz:

```sh
./setup.sh
./scripts/run-local.sh
```

El primer comando crea `backend/.venv`, instala el lock de Python, instala el lock de la web y compila el cliente. El segundo usa `.local/demo/aros-demo.db`, vacía la configuración de proveedores para ese proceso y escucha en `127.0.0.1:8000`. La aplicación y sus workers se detienen con Ctrl+C.

No coloques datos privados ni un `.env` en el directorio de demo. Si el puerto 8000 está ocupado, detén la otra demo antes de arrancar. Para reiniciar los ejemplos, detén primero todos los procesos y mueve `.local/demo` a una carpeta de respaldo; el siguiente arranque crea una base nueva. Esto afecta solamente a la demo local.

## Configuración

`.env.example` documenta las variables. Los valores vacíos no son credenciales de ejemplo utilizables. Una instancia con red requiere una clave Fernet válida, contraseña de al menos 16 caracteres, URL HTTPS y demo desactivada. Las claves de cifrado de una instalación deben conservarse para leer sus secretos cifrados.

El backend lee el entorno del proceso y el `.env` del directorio desde el que se ejecuta. La plantilla Docker usa exclusivamente `deploy/.env`; el arranque local define sus valores expresamente.

## Servidor Docker: plantilla para validar en tu entorno

Esta distribución incluye una plantilla para Docker Engine y Compose. La validación local de esta entrega no equivale a haber desplegado y restaurado un servidor Docker nuevo; consulta `VALIDATION.md`. No utilices una base real para la primera prueba.

Necesitas un servidor propio, un hostname que resuelva hacia él y los puertos 80/443 disponibles. En una copia del repositorio situada en ese servidor:

```sh
python3 scripts/prepare_env.py --domain aros.example.com --email admin@example.com
./scripts/deploy.sh
```

Sustituye el dominio y el correo por los de tu instalación. El generador crea `deploy/.env` con permisos 0600 y secretos aleatorios; se detiene si ya existe. Consulta `ADMIN_PASSWORD` en privado en ese archivo para el primer acceso. No lo pegues en chats ni incidencias.

La plantilla compila la web y el backend, inicia PostgreSQL, aplica migraciones y arranca API, workers y Caddy. Los servicios de proveedores son opcionales y no se habilitan en el despliegue básico. Comprueba HTTPS, autenticación, creación de una tarea, aislamiento entre cuentas y recuperación antes de usar datos reales.

## Actualizaciones y copias

Antes de actualizar una instancia con datos:

```sh
./scripts/backup.sh
./scripts/verify-backup.sh /ruta/absoluta/a/la/copia.dump
```

La verificación restaura en una base temporal y la elimina al terminar; no restaura sobre producción. Guarda aparte, de forma privada, `deploy/.env` y cualquier estado de proveedor externo. Una copia de base sin la clave de cifrado no basta para recuperar secretos.

Revisa el changelog y la compatibilidad de migraciones antes de ejecutar `deploy.sh` sobre una instalación existente. Volver a una imagen anterior no revierte automáticamente un cambio de esquema.

## iOS

El cliente SwiftUI se conserva como código experimental. Sigue `ios/README.md`. Las comprobaciones de modelos, sintaxis o contratos HTTP no acreditan compilación, firma o ejecución en iPhone. El servidor se configura explícitamente; no existe una dirección privada predeterminada.
