# Perfil privado aros-planner

`entry.json`, `AGENTS.md` y `setup.cjs` definen el perfil opcional que decide JSON para las capacidades internas de Aros. Usa exclusivamente el modelo definido por `OPENCLAW_MODEL`, sin fallbacks ni skills; hereda la autenticación existente, sin copiar OAuth. El registro permite solo `session_status` por compatibilidad. No permite herramientas de host, navegador ni WhatsApp. Las solicitudes del backend usan `tools: []` y `tool_choice: "none"`; las llamadas a funciones nativas se rechazan.

Desde `/opt/aros/deploy`, tras revisar y autorizar la configuración, se pueden preparar los tres archivos en el contenedor existente:

```sh
docker compose -f compose.yaml -f compose.prebuilt.yaml cp openclaw/planner/entry.json openclaw:/tmp/aros-planner-config.json
docker compose -f compose.yaml -f compose.prebuilt.yaml cp openclaw/planner/AGENTS.md openclaw:/tmp/aros-planner-AGENTS.md
docker compose -f compose.yaml -f compose.prebuilt.yaml cp openclaw/planner/setup.cjs openclaw:/tmp/aros-planner-setup.cjs
docker compose -f compose.yaml -f compose.prebuilt.yaml exec -T openclaw node /tmp/aros-planner-setup.cjs
```

El script exige que ya exista el agente `aros` y valida la política del nuevo perfil. Lee `/home/node/.openclaw/openclaw.json`, conserva una copia `.before-planner-<fecha>` con permisos `0600`, actualiza la entrada sin duplicar la herramienta de registro y reemplaza la configuración mediante un archivo temporal. Repetirlo conserva el mismo estado configurado; cada ejecución genera su propia copia de seguridad. La copia puede contener credenciales existentes: permanece privada y no debe incluirse en entregas ni logs. El script no imprime secretos ni instala componentes.

El workspace queda en `/home/node/.openclaw/workspaces/aros-planner`. Copia allí `AGENTS.md` desde `/tmp/aros-planner-AGENTS.md` y escribe `SOUL.md` y `BOOTSTRAP.md` para evitar onboarding. La aplicación de la configuración al gateway activo y su validación operativa pertenecen al procedimiento de despliegue; ejecutar el script no demuestra por sí solo que una inferencia funcione. No ejecutar una restauración sobre producción para comprobar el backup.
