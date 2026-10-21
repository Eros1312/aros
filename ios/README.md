# Aros para iPhone · experimental

Cliente SwiftUI para iOS 17+. El producto principal de esta primera versión es la web; este directorio contiene código experimental que todavía requiere compilación con el SDK de iOS y verificación en simulador y dispositivo. No hay una distribución de App Store ni una aplicación firmada incluida.

## Abrir el proyecto

1. Instala Xcode completo y abre `Aros.xcodeproj`.
2. Elige tu Team de firma y cambia el identificador de aplicación `com.aros.personal.demo` por uno propio.
3. Selecciona un simulador iOS 17 o posterior. El catálogo de iconos está vacío: añade tu icono antes de distribuir una aplicación.
4. Explora los datos sintéticos o abre Ajustes e introduce la dirección de **tu** instalación. No hay un servidor remoto predeterminado.

El simulador admite `http://127.0.0.1:8000` para una API local con `DEMO_MODE=true`. Un iPhone físico necesita un servidor HTTPS accesible. No publiques el modo demo en Internet. El formulario solo envía credenciales cuando pulsas Conectar; la sesión usa Keychain.

## Comprobaciones

```sh
./check.sh
# Con una API de demostración local y desechable en 127.0.0.1:8000:
./check-api.sh
```

`check.sh` comprueba modelos y vistas compartidas con el SDK macOS, analiza sintaxis iOS y valida el proyecto. Aplica una compatibilidad del macro State únicamente en archivos temporales. **No sustituye a una compilación con el SDK iOS.**

`check-api.sh` utiliza URLSession, exige una cuenta demo en localhost y crea, actualiza y elimina exclusivamente su propio recurso de prueba. No ejecuta agentes ni llama a proveedores de IA. El resultado solo acredita los contratos comprobados en esa API local.

La app incluye datos, calendario, archivos, conversaciones, conexiones, dictado, Keychain y bloqueo del dispositivo. Los recordatorios se programan localmente con datos descargados; no hay notificaciones push del servidor. El dictado puede usar servicios de Apple según el dispositivo. Consulta también las limitaciones del README raíz antes de utilizarla con datos reales.

Código fuente: <https://github.com/Eros1312/aros>. Licencia: AGPL-3.0-only.
