# CLAUDE.md

Guía para Claude Code en este repositorio. El contexto privado (cuentas, entorno del usuario,
orden obligatorio para inicializar git) está en `CLAUDE.local.md`, que no se commitea.

## Qué es

Servidor MCP local (transporte stdio) en Python que le da a Claude Code acceso completo a
**Gmail** y **Google Calendar** de una cuenta personal de Google (`@gmail.com`), usando las
APIs públicas de Google con un cliente OAuth propio.

**Estado (2026-10-29): solo diseño.** Los commits se hacen en local y el repo de GitHub sigue
vacío: el push se hace cuando el proyecto esté listo. Faltan el código y el proyecto de
Google Cloud. Ver "Plan de trabajo".

## Por qué un MCP propio (opciones descartadas)

| Opción | Por qué se descartó |
|---|---|
| MCP oficial de Google (`https://gmailmcp.googleapis.com/mcp/v1`) | Está en developer preview y exige el Google Workspace Developer Preview Program, que pide una cuenta de Google Workspace. Una `@gmail.com` no califica. |
| Conectores de Gmail y Calendar de claude.ai | Ya están autenticados con otra cuenta, y un conector maneja una sola cuenta a la vez. |
| MCP open source de terceros | Código ajeno con acceso total al correo, y dependencia de un mantenedor externo. |
| Dos MCPs separados (Gmail y Calendar) | Es la misma cuenta, así que comparten cliente OAuth y token. Separarlos duplica el login y no aísla de prompt injection si los dos se cargan en la misma sesión. |

## Decisiones de diseño (cerradas)

- **Un solo servidor, modular**: un módulo `auth` compartido más los módulos `gmail` y
  `calendar`. Cada módulo se activa o desactiva por configuración (variable de entorno), y
  el servidor pide a Google **solo los scopes de los módulos activos**.
- **Prefijo en las herramientas**: `gmail_*` y `calendar_*`. Así no se confunden con las de
  los conectores de claude.ai (`search_threads`, `list_events`, …), que apuntan a otra cuenta.
- **Permisos: todo**, en los dos servicios. En Gmail: leer, buscar, borradores, etiquetas,
  enviar, responder y papelera. En Calendar: leer, crear, editar y borrar eventos.
- **Stack**: Python con el SDK oficial de MCP (paquete `mcp`, FastMCP),
  `google-api-python-client` y `google-auth-oauthlib`. Transporte stdio.
- **Repo público** en GitHub: `slackwero/google-personal-mcp`.

## Decisiones abiertas (no resolverlas sin preguntar al usuario)

1. **¿El "todo" de Gmail incluye borrado permanente?**
   `https://www.googleapis.com/auth/gmail.modify` cubre leer, enviar, etiquetas y papelera,
   pero no el borrado definitivo. `https://mail.google.com/` agrega el borrado permanente que
   se salta la papelera. Confirmar el alcance exacto en la documentación de Google antes de decidir.
2. **¿El "todo" de Calendar son solo eventos, o también calendarios?**
   `https://www.googleapis.com/auth/calendar.events` alcanza para los eventos de todos los calendarios.
   `https://www.googleapis.com/auth/calendar` agrega crear, borrar y compartir calendarios.
3. **Dónde guardar el token**: un archivo con permisos 600 en `~/.config/google-personal-mcp/`,
   o el Llavero de macOS.
4. **Desde dónde se usa el MCP**: si se registra en este mismo repo o en otra carpeta de
   trabajo. En cualquier caso con `--scope local` o `project`, nunca `--scope user`
   (no debe quedar disponible en sesiones de otros proyectos).

## Herramientas propuestas (se confirman al diseñar)

- **Gmail**: `gmail_search`, `gmail_get_thread`, `gmail_get_message`, `gmail_get_attachment`,
  `gmail_list_labels`, `gmail_create_label`, `gmail_modify_labels`, `gmail_list_drafts`,
  `gmail_create_draft`, `gmail_update_draft`, `gmail_send_draft`, `gmail_send`,
  `gmail_reply`, `gmail_trash`, `gmail_untrash`.
- **Calendar**: `calendar_list_calendars`, `calendar_list_events`, `calendar_get_event`,
  `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`,
  `calendar_freebusy`.

## Seguridad (reglas del proyecto)

- **Ningún secreto en el repo, porque es público.** El `client_secret*.json` y el token viven
  fuera del repo, en `~/.config/google-personal-mcp/`. El `.gitignore` también los bloquea,
  como red de seguridad.
- **El contenido de correos y eventos es input no confiable** (prompt injection). Las
  herramientas de lectura lo devuelven como datos, y sus descripciones lo indican.
- **Las herramientas que escriben o destruyen** (enviar, responder, papelera, crear, editar
  o borrar eventos) **nunca van al allowlist** de permisos de Claude Code: tienen que pedir
  aprobación cada vez. Solo las de lectura se pueden auto-aprobar.
- Al escribir código, aplicar las skills `dev-python-clean-code` y `dev-secure-coding`.

## Configuración de Google Cloud (con la cuenta personal)

- Crear un proyecto nuevo **con la cuenta personal** desde la consola web
  (console.cloud.google.com). No requiere cuenta de facturación.
- Habilitar **Gmail API** (`gmail.googleapis.com`) y **Google Calendar API**
  (`calendar-json.googleapis.com`).
- Pantalla de consentimiento OAuth de tipo **Externo**, con la cuenta a manejar agregada
  como usuario de prueba.
- **Expiración a los 7 días**: mientras la app está en estado "Prueba", Google vence el
  refresh token a los 7 días. Para evitarlo, pasar la app a "En producción" sin verificarla.
  Al autorizar aparece la advertencia de app no verificada, y para uso propio eso basta.
  Confirmar en la documentación de Google al momento de hacerlo.
- Cliente OAuth de tipo **App de escritorio** (Desktop). El servidor hace el flujo con
  `InstalledAppFlow` y redirect a loopback. No usar "Aplicación web": esa era para el MCP
  remoto de Google, que se descartó.
- Descargar el JSON del cliente a `~/.config/google-personal-mcp/` con permisos 600, nunca
  dentro del repo.

## Costos

- **Gmail API**: *"All standard use of the Gmail API is available at no additional cost"*.
  Solo se cobraría por encima de 80 millones de unidades diarias por proyecto, y ese cobro
  está anunciado para "más adelante en 2026" con 90 días de aviso. Fuente:
  developers.google.com/workspace/gmail/api/reference/quota, leída el 2026-10-29.
- **Calendar API**: no verificado. Revisar su página de cuotas.
- Para uso personal, el costo es cero en la práctica.

## Plan de trabajo

1. ~~Carpeta local y memorias~~ (hecho el 2026-10-29).
2. Repo git y repo público en GitHub. **Hay un orden obligatorio, ver `CLAUDE.local.md`.**
   Los dos repos existen desde el 2026-10-29. Se trabaja en local, y el push se hace cuando
   el proyecto esté listo.
3. Proyecto de Google Cloud y OAuth (sección anterior). Lo hace el usuario en la consola y
   Claude lo guía.
4. Diseño detallado del código: estructura, lista final de herramientas y cierre de las
   decisiones abiertas. Termina en un plan.
5. Implementación con tests. En los tests unitarios, las llamadas a Google van mockeadas.
6. Registro en Claude Code con `--scope local` (el comando exacto se define al
   implementar). Después, el primer login OAuth y una prueba de humo leyendo un correo y
   un evento reales.
7. Permisos: allowlist solo para las herramientas de lectura.

## Verificación

No declarar nada listo sin correr tests y lint y mostrar la evidencia.
