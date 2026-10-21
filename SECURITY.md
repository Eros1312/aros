# Seguridad

Aros 0.1.x es una versión inicial mantenida por su autor. No cuenta con una auditoría independiente ni un SLA de respuesta. Las correcciones se publicarán en la rama principal y en nuevas versiones.

## Informar de una vulnerabilidad

No publiques claves, datos personales o instrucciones de explotación contra una instancia real en una incidencia pública. Utiliza **Security → Advisories → Report a vulnerability** en GitHub cuando esté habilitado. Si ese canal aún no está disponible, abre una incidencia que únicamente solicite un canal privado; no incluyas los detalles sensibles hasta acordarlo.

Incluye, mediante el canal privado, versión afectada, impacto, reproducción con datos sintéticos y una posible corrección. No pruebes instalaciones de otras personas sin autorización.

## Límites operativos

- La demo solo sirve para uso local y contiene acceso de demostración. No la publiques ni la conectes a cuentas privadas.
- Para una instancia accesible por red usa HTTPS, `APP_ENV=production`, `DEMO_MODE=false`, contraseña inicial fuerte, clave Fernet propia y acceso mínimo.
- Guarda `.env`, claves, datos, backups y logs fuera del código compartido. Las copias también pueden contener información sensible.
- Configura cada conector expresamente. Las claves de API pueden generar cargos. No hay activación automática de servicios de pago.
- El contenido recuperado es entrada no fiable. Los modelos pueden equivocarse; revisa propuestas y no amplíes sus permisos para resolver fallos.
- Las tareas y los documentos se aíslan por propietario. La cobertura automatizada reduce regresiones, pero no demuestra ausencia de vulnerabilidades.
- Conserva copias consistentes y verifica su restauración en un entorno aislado antes de actualizar una instancia con datos reales.

El modo de desarrollo y los servicios simulados de tests no constituyen una configuración segura para un servidor público.
