# Contribuir a Aros

Gracias por ayudar a que otras personas puedan instalar y mantener Aros. Se aceptan incidencias y pull requests en español o inglés.

## Empezar

1. Sigue el README y prueba la demo con datos sintéticos.
2. Abre una incidencia con el resultado esperado, el resultado observado y pasos mínimos para reproducirlo. Incluye sistema operativo y versiones, sin información personal.
3. Para cambios de alcance amplio, describe primero el problema y la solución propuesta.
4. Crea una rama, realiza un cambio acotado y ejecuta `./scripts/verify.sh`.
5. En la pull request explica el problema, el comportamiento final y qué comprobaciones realizaste.

## Criterios de revisión

- Conservar los límites de propietario y los controles de acceso. Una referencia en un documento no autoriza una acción.
- No habilitar conexiones, llamadas de pago o escrituras externas implícitamente.
- Mantener accesibilidad por teclado, foco visible y etiquetas comprensibles.
- Añadir pruebas de regresión cuando se corrija un fallo de comportamiento, especialmente permisos, persistencia o concurrencia.
- Usar fixtures sintéticos. No copiar documentos, credenciales, capturas de cuentas, tokens de sesión ni catálogos sin permiso.
- No presentar datos simulados, builds locales o pruebas de sintaxis como validación de servicios reales o dispositivos.
- Las migraciones deben conservar los datos existentes y documentar recuperación y compatibilidad.

No se exige un CLA separado. Al enviar una contribución debes tener derecho a distribuirla bajo la licencia AGPL-3.0-only del proyecto. Conserva los avisos de autoría de terceros cuando corresponda.

## Dependencias

El backend declara dependencias directas en `backend/requirements.txt` y fija el entorno comprobado en `backend/requirements-lock.txt`. La web usa `web/pnpm-lock.yaml`. Actualiza los locks intencionadamente, revisa los cambios de licencia y repite la validación. No incluyas dependencias instaladas en el repositorio.

## Trato entre colaboradores

Discute el código y las decisiones con respeto. No se aceptan acoso, amenazas ni publicación de datos personales. El mantenedor puede moderar contenido abusivo y cerrar contribuciones fuera de alcance. Este proyecto se mantiene sin promesa de soporte o plazo de respuesta.
