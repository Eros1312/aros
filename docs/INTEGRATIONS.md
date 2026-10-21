# Conectores y modelos

La demo se puede probar sin conectar ninguna cuenta. Las pruebas automáticas simulan servicios externos; no demuestran que una cuenta concreta esté autorizada ni que un proveedor conserve su API.

## Modos de respuesta

| Configuración | Comportamiento |
| --- | --- |
| Sin proveedor | Resumen local limitado basado en reglas; no se presenta como modelo de IA |
| `OPENAI_API_KEY` y `OPENAI_MODEL` explícitos | Ruta de chat directo cuando el operador la configura |
| `OPENCLAW_URL`, `OPENCLAW_TOKEN` y `OPENCLAW_MODEL` explícitos | Gateway de agentes opcional, con el modelo que el operador haya habilitado |

No se elige una suscripción ni un modelo de pago por defecto. El identificador de modelo debe existir y estar autorizado en la cuenta del operador. Las configuraciones incompletas no deben utilizarse como prueba de una conexión funcional. Un cambio de modelo no modifica retroactivamente trabajos ya encolados.

Configura una sola ruta de IA: el arranque rechaza usar simultáneamente la clave directa de OpenAI y una URL de OpenClaw. Después de cambiar la configuración, reinicia API y workers, elige el modelo configurado en Ajustes y activa el consentimiento de IA externa. Los usuarios ya creados conservan su selección anterior hasta que la cambien; añadir una credencial no cambia esa selección automáticamente.

## OpenClaw

El código incluye un adaptador de herramientas y una plantilla mínima en `deploy/openclaw`. La integración completa requiere configurar y autorizar el gateway siguiendo su documentación oficial. La plantilla Docker se prepara así, después del despliegue básico:

1. Añade en tu archivo privado `deploy/.env` un `OPENCLAW_MODEL` que hayas elegido y un `OPENCLAW_TOKEN` aleatorio de al menos 32 caracteres. Mantén `OPENCLAW_URL` vacío durante la preparación.
2. Ejecuta `./scripts/enable-openclaw.sh`. Prepara la configuración y arranca únicamente el gateway opcional; no conecta una cuenta ni adquiere una suscripción. Los canales personales permanecen desactivados.
3. Autoriza el proveedor elegido con su procedimiento oficial. Si admite el flujo de inicio de sesión de OpenClaw, el envoltorio `./scripts/openclaw-login.sh IDENTIFICADOR_DEL_PROVEEDOR` lo ejecuta sin cambiar el modelo predeterminado.
4. Cuando el gateway esté autorizado, configura `OPENCLAW_URL=http://openclaw:18789` en `deploy/.env` y recrea API y workers con `docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --force-recreate api worker agent_worker`.
5. Elige el modelo en Ajustes, activa el consentimiento y prueba una consulta con datos sintéticos. Un gateway que responde no demuestra que el proveedor permita ejecutar ese modelo.

Este flujo Docker no se ha ejecutado en la validación local de la versión; consulta [VALIDATION.md](VALIDATION.md). Conserva las herramientas limitadas al alcance de Aros y verifica el resultado de cada acción.

WhatsApp y otros canales personales no están activados en la distribución. Añadirlos implica configurar sus servicios por separado y revisar su privacidad y permisos.

## Datos externos

- **Google:** requiere una aplicación OAuth propia y consentimiento del usuario. La presencia del formulario no significa que Gmail, Drive o Calendar estén conectados.
- **Aprendizaje:** organiza cursos, documentos y objetivos añadidos por el usuario. No requiere una cuenta educativa ni incluye conectores de una institución concreta.
- **Diseño/Canva:** necesita la aplicación y los permisos del operador. Para generar propuestas requiere además `OPENCLAW_DESIGN_MODEL` explícito; si está vacío, esa generación no está disponible. No se incluyen diseños privados ni aprobaciones heredadas.
- **GitHub y noticias:** las fuentes y su sincronización se habilitan explícitamente. Cada material conserva su licencia de origen.
- **Recursos:** permite organizar documentos, notas y enlaces propios. No descarga catálogos externos automáticamente.
- **Métricas de servidor:** describen el entorno donde corre el backend o un agente de servidor configurado por el operador.

Una fuente puede cambiar, limitar peticiones o revocar acceso. La interfaz y el informe de validación deben distinguir desconectado, pendiente, fallido y comprobado. No sustituyas un fallo por datos inventados.
