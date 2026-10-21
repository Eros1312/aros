# Validación de la versión 0.1.0

Comprobación realizada el **21 de octubre de 2026** sobre la distribución Aros. El historial de pruebas de la aplicación privada no se utiliza como prueba de esta versión.

## Alcance

Entorno local: macOS ARM64, Python 3.12.14, Node.js 24.21.0 y pnpm 11.25.0. Los conectores externos se prueban con respuestas simuladas.

| Comprobación | Resultado |
| --- | --- |
| Instalación backend y resolución del lock | Correcta; `pip check` sin conflictos |
| Suite backend generalizada | **476 pruebas aprobadas**; tras ajustar una condición de migración, **8 pruebas de migración e instalación aprobadas**, incluida una nueva regresión (477 pruebas en la colección final); 6 advertencias de deprecación de dependencias |
| Migraciones SQLite desde una base nueva y comparación de esquema | Correctas; transición de áreas antiguas verificada con conservación de archivos y referencias |
| Arranque/demo y ciclo de worker con conexiones de red bloqueadas | Correctos con datos sintéticos |
| Control de acciones del servidor compartido | Administrador autorizado; otro usuario recibe 403 sin llamada remota |
| Instalación web con lock y versión fijada de pnpm | Correcta |
| TypeScript, lógica de flujos y ciclo de voz | Correctos |
| Regresiones DOM | **23 aprobadas** |
| Build estático Next.js 16.3.8 | Correcto |
| Pruebas de navegador | **19 comprobaciones aprobadas**; seis rutas a 390, 768 y 1280 px; navegación, formularios, creación de enlaces en Recursos, biblioteca y agenda de Eventos |
| Errores JavaScript/hidratación en esos recorridos | No detectados |
| Swift: modelos, vistas compartidas, sintaxis iOS y configuración del proyecto | Correctos con Command Line Tools |
| Cliente URLSession contra API de demo aislada | Autenticación, DTOs, lectura, creación, versión y borrado confirmado correctos |
| Auditoría de dependencias Python (`pip-audit`) | Sin vulnerabilidades conocidas tras actualizar cinco dependencias |
| Auditoría web de producción (`pnpm audit --prod`) | Sin vulnerabilidades conocidas tras actualizar Next.js y source-map-js |
| Shell, plantillas Compose y adaptador de herramientas | Sintaxis/estructura y comprobaciones de límites correctas |
| Revisión del contenido publicable | Véase [SANITIZATION_REPORT.md](../SANITIZATION_REPORT.md) |

Las auditorías consultan catálogos de vulnerabilidades conocidos en la fecha indicada. Un resultado limpio no certifica ausencia de problemas futuros ni sustituye una revisión de seguridad independiente.

## Automatización

`.github/workflows/verify.yml` define comprobaciones backend, migraciones, web y fuente Swift para pushes y pull requests. Los resultados de GitHub Actions deben consultarse en el repositorio: una ejecución local no se presenta como una ejecución remota correcta.

## Límites

No se atribuyen usuarios externos, descargas o uso comunitario a los ejemplos de demo. No se ha realizado una auditoría independiente. Una comprobación Swift de modelos o sintaxis no equivale a compilar, firmar o probar una aplicación iOS en un dispositivo.

El equipo de preparación no dispone de Docker Engine ni del SDK completo de iOS. **No se ha ejecutado un despliegue Docker nuevo, una restauración PostgreSQL real, una conexión de proveedor en producción ni una compilación/ejecución nativa en iPhone**. Se conservan estas capacidades como plantillas o código, con su validación operativa pendiente.
