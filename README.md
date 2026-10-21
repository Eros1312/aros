# Aros

**Un espacio de trabajo personal que puedes alojar en tu propio servidor.**

Aros reúne tareas, documentos, calendario y conversaciones en una aplicación web. Su motor de agentes conserva el propietario, el alcance, el estado y la evidencia de cada trabajo. Las propuestas que necesitan aprobación pasan por un flujo explícito antes de aplicarse.

**English:** Aros is a self-hosted personal workspace with a FastAPI backend and a Next.js client. It combines tasks, documents, calendar and persistent agent workflows with scoped permissions and verifiable results. The local demo runs without an AI account. The interface is currently in Spanish; contributions and issues in English are welcome.

**Estado: primera versión pública, 0.1.0.** Procede de una aplicación personal y todavía conserva áreas especializadas. La web es el cliente principal. El cliente iOS es experimental y se entrega como código fuente. No se afirma adopción externa ni compatibilidad universal con servicios de terceros.

## Probar en local

Requisitos: Git, Python **3.12**, Node.js **24** y pnpm **11.25.0**. Para instalar pnpm: `npm install --global pnpm@11.25.0`.

```sh
git clone https://github.com/Eros1312/aros.git
cd aros
./setup.sh
./scripts/run-local.sh
```

Abre [http://127.0.0.1:8000](http://127.0.0.1:8000) y elige **Explorar con datos de ejemplo**. Se crean datos sintéticos en `.local/demo/`; no hace falta una clave de API. El modo local escucha únicamente en el propio equipo y no está preparado para exponerse a Internet. Pulsa Ctrl+C para detenerlo.

Si `python3` no es Python 3.12, configura la ruta al intérprete:

```sh
AROS_PYTHON=/ruta/a/python3.12 ./setup.sh
```

La instalación descarga dependencias de los registros oficiales. Una vez instaladas, la demo no activa proveedores de IA ni conectores de cuentas. Sin proveedor, el chat utiliza respuestas locales limitadas; los trabajos de IA requieren configurar un gateway compatible.

## Qué incluye

- Tareas, proyectos, notas, documentos y calendario con historial y aislamiento por usuario.
- Biblioteca de archivos y búsqueda; lectura de PDF y formatos de documentos compatibles.
- Conversaciones persistentes y trabajos de agentes con estados, permisos y comprobación de resultados.
- Propuestas revisables, control de acceso a fuentes y revocación de acceso a contenido derivado.
- Conectores opcionales para servicios externos, cuya configuración y autorización corresponden al operador.
- Áreas de estudio, diseño y modelos 3D heredadas de la aplicación original. Los catálogos personales y los documentos de terceros no se distribuyen.

El proyecto no incluye modelos de IA, suscripciones, credenciales, acceso a cuentas ni material privado. Una prueba con respuestas simuladas no acredita la conexión a un proveedor real.

## Desarrollo y comprobaciones

```sh
./scripts/verify.sh
```

Ejecuta las pruebas backend, TypeScript, comprobaciones de lógica y DOM, y la compilación web. Los tests usan datos sintéticos, servicios simulados y bases temporales. Consulta [VALIDATION.md](docs/VALIDATION.md) para conocer las comprobaciones realizadas sobre esta versión y sus límites.

| Carpeta | Contenido |
| --- | --- |
| `backend/app` | API FastAPI, servicios, permisos, agentes y workers |
| `backend/migrations` | Migraciones Alembic |
| `backend/tests` | Regresiones y fixtures sintéticos |
| `web` | Cliente Next.js/React con exportación estática |
| `ios` | Cliente SwiftUI experimental |
| `deploy` | Plantillas de despliegue y OpenClaw opcional |
| `scripts` | Arranque, verificación, despliegue y copias |

## Desplegar y configurar

Consulta [Instalación y operación](docs/INSTALLATION.md), [Arquitectura](docs/ARCHITECTURE.md) y [Conectores](docs/INTEGRATIONS.md). Para publicar una instancia utiliza autenticación, HTTPS, una clave de cifrado propia y `DEMO_MODE=false`. Los datos de una instalación anterior no se migran automáticamente a esta distribución.

## Participar

Las aportaciones más útiles ahora son pruebas de instalación en otros equipos, informes reproducibles, mejoras de accesibilidad y traducción, y ejemplos de conectores. Lee [CONTRIBUTING.md](CONTRIBUTING.md), [el plan de trabajo](docs/ROADMAP.md) y [SECURITY.md](SECURITY.md). No adjuntes documentos personales, claves o bases de datos a las incidencias.

Mantenedor principal: [Eros1312](https://github.com/Eros1312). Los errores y propuestas se gestionan en [Issues](https://github.com/Eros1312/aros/issues).

## Licencia

Copyright 2026 Eros1312 y colaboradores. El código original de Aros se distribuye bajo **AGPL-3.0-only**: consulta [LICENSE](LICENSE). Las dependencias conservan sus propias licencias; [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) identifica los componentes principales. Aros no está afiliado a OpenAI, OpenClaw ni a los servicios con los que puede integrarse.
