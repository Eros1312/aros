# Pruebas de la integración web

Los datos de estas pruebas son ejemplos sintéticos. No necesitan cuentas externas ni llamadas a modelos.

## Comprobaciones ejecutables

Desde `web`, tras `pnpm install --frozen-lockfile`:

```sh
pnpm test:unit
pnpm test:dom
pnpm typecheck
pnpm build
```

`test:unit` verifica los alias de navegación, el alcance por IDs, la retirada de contexto, los vínculos explícitos de proyecto y el ciclo de voz. `test:dom` monta los componentes React con JSDOM, una API simulada y almacenamiento de sesión. Comprueba contexto persistente y revocado, navegación real desde Hoy a un proyecto, cambio de proyecto con historial y borrador separados, adjuntos vinculados y seleccionados, tareas vinculadas, errores, archivo, teclado y jerarquía semántica. También comprueba que el uso del modelo muestra la última medición reportada por decisión, sin sumar ni inventar campos.

JSDOM no es un navegador real: estas pruebas **no acreditan** distribución visual, contraste, dimensiones táctiles, animaciones, accesibilidad completa ni capturas de pantalla.

## Comprobación con navegador

`workflow.browser.cjs` contiene un recorrido de navegador aislado que comprueba 390, 768 y 1280 px, teclado, cuadros de recurso, estados vacíos/de error y produce capturas. Necesita Playwright instalado en el entorno de pruebas (`PLAYWRIGHT_MODULE` permite señalar una instalación existente), un Chromium instalado por Playwright (o `AROS_CHROMIUM` apuntando a un ejecutable compatible) y una API de ejemplo que publique `web/out`.

La API debe usar `APP_ENV=development`, `DEMO_MODE=true`, una base local aislada, ningún worker de agentes/automatizaciones y ninguna credencial de proveedor. El script rechaza destinos que no sean localhost/127.0.0.1, verifica la cuenta de ejemplo y bloquea las peticiones a dominios externos. La solicitud de chat se intercepta para comprobar su contrato sin iniciar un modelo.

El proyecto de prueba debe tener un documento, una tarea y un resultado verificado vinculados. Indica su ID mediante `AROS_TEST_PROJECT_ID`.

Puedes crear esa muestra con `backend/tests/prepare_workflow_demo.py`, ejecutado desde `backend` con `PYTHONPATH=.` y las variables de ejemplo anteriores. Exige una base SQLite **nueva**, con nombre `aros-ui-test-*.db`, y sin configuración de proveedores; rechaza archivos existentes. Imprime un JSON con `project_id`, `source_id`, `job_id` y `provider_calls: 0`. Después arranca únicamente la API apuntando a la misma base y utiliza ese `project_id` en el recorrido. No arranques workers. El fixture usa decisiones prefijadas y no demuestra una llamada real a un modelo.

```sh
AROS_TEST_URL=http://127.0.0.1:8000 \
AROS_TEST_PROJECT_ID=ID_DEL_PROYECTO_DE_EJEMPLO \
AROS_EVIDENCE_DIR=/tmp/aros-browser-evidence \
PLAYWRIGHT_MODULE=/ruta/a/playwright \
node tests/workflow.browser.cjs
```

Las pruebas de componentes usan datos sintéticos y no acreditan conexiones reales, distribución visual ni cambios en producción. El recorrido de navegador guarda un informe JSON y capturas en `AROS_EVIDENCE_DIR`; revisa el informe de cada ejecución antes de dar por verificada una versión.
