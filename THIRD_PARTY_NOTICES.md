# Componentes y atribuciones

El código original de Aros se publica bajo AGPL-3.0-only. Las dependencias no se relicencian: cada una conserva sus avisos y condiciones. No se incluyen sus paquetes instalados en el repositorio.

| Componente | Uso | Referencia |
| --- | --- | --- |
| FastAPI, Uvicorn, Pydantic y SQLAlchemy | API, configuración y persistencia | Paquetes fijados en `backend/requirements-lock.txt` |
| Alembic y psycopg | Migraciones y PostgreSQL | Paquetes fijados en el lock backend |
| PyMuPDF/MuPDF | Lectura, vista previa y creación de documentos | [Licencia AGPL/comercial de PyMuPDF](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) |
| pypdf, cryptography y defusedxml | Documentos, cifrado y XML | Paquetes fijados en el lock backend |
| Next.js, React, TypeScript y Lucide | Interfaz web | `web/package.json` y `web/pnpm-lock.yaml` |
| OpenClaw | Gateway opcional, instalado por el operador | [Repositorio upstream](https://github.com/openclaw/openclaw) |
| PostgreSQL y Caddy | Servicios opcionales de despliegue | Imágenes declaradas en `deploy/compose.yaml` |

Los SVG geométricos de la interfaz forman parte del código original de este proyecto. Los documentos de prueba se generan a partir de texto y geometría sintéticos. No se distribuyen documentos universitarios, música, fotografías de terceros, fuentes comerciales ni el catálogo personal de modelos 3D.

El usuario de una instancia conserva la responsabilidad sobre los archivos que importa y los permisos de sus servicios conectados. La licencia del código no concede derechos sobre esos materiales.
