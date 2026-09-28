# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Qué es

Bot que corre una vez por día (cron, 8am) para cargar 3 tipos de cambio en
Tourplan NX: Dólar MEP, Dólar Oficial y Dólar Emisivo. Scrapea las
cotizaciones, valida que sean razonables, y las carga automatizando el
navegador con Playwright. Si algo falla, corta y avisa por email.

## Comandos

```bash
# Setup (Linux/servidor)
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install --with-deps chromium   # instala Chromium para Playwright
cp .env.example .env              # completar credenciales

# Correr el proceso completo (lo que ejecuta cron)
python main.py

# Pre-autenticar en Tourplan UNA vez y guardar la sesión (data/tourplan_session.json).
# Útil para dejar la sesión lista y que las corridas la reutilicen sin loguear.
python tourplan_automation.py

# Probar solo el scraping, sin tocar Tourplan
python scrapers.py

# Tests (solo cubren los parsers de scrapers.py, contra fixtures HTML —
# no requieren red ni Playwright instalado)
pip install -r requirements-dev.txt
pytest -v
```

No hay linter ni build configurados. La automatización de Tourplan
(`tourplan_automation.py`) no tiene tests — la verificación es manual,
corriendo con `HEADLESS=false` en `.env` para ver el navegador en acción.

## Arquitectura

Pipeline lineal de 4 pasos orquestado por [main.py](main.py). Cada paso está
en su propio módulo y define su propia excepción; `main()` captura cada una y,
ante cualquier fallo, envía un email de alerta y retorna código de salida `1`
**antes** de dejar Tourplan a medio actualizar. El orden importa: la
validación corre siempre antes de tocar Tourplan.

1. **[scrapers.py](scrapers.py)** → `obtener_cotizaciones()` devuelve
   `ResultadoScraping` (dataclass que envuelve `Cotizaciones` +
   `fuentes_fallback: list[str]`). Usa `requests` + BeautifulSoup (la fuente
   es HTML de servidor, no requiere navegador). Extrae Dólar MEP y Dólar
   Oficial de dolarhoy.com; calcula `emisivo = oficial + 10`. Si dolarhoy.com
   no se puede parsear, cae automáticamente a dolarapi.com como respaldo
   (`_obtener_con_fallback`) y lo señaliza en `fuentes_fallback` sin cortar
   la carga; si las dos fuentes fallan, recién ahí lanza `ScrapingError`.
2. **[validacion.py](validacion.py)** → `validar_y_registrar()` compara cada
   valor contra el último cargado en `data/historial_cotizaciones.json`; si la
   variación supera `UMBRAL_VARIACION_PCT` (15% por defecto) lanza
   `ValidacionError` y corta. Si pasa, persiste el nuevo valor como referencia
   del día siguiente. Excepción: `ValidacionError`.
3. **[tourplan_automation.py](tourplan_automation.py)** →
   `cargar_tipos_de_cambio()` automatiza el flujo con Playwright síncrono.
   Excepción: `TourplanAutomationError`. Guarda un screenshot en `logs/` ante
   cualquier fallo.
4. **[alertas.py](alertas.py)** → `enviar_alerta()` manda email vía SMTP. Si
   falta config SMTP, loguea un warning y no falla (nunca rompe el proceso).

Si los 4 pasos terminan OK, `main()` pinguea `HEARTBEAT_URL` con
**[heartbeat.py](heartbeat.py)** → `enviar_heartbeat()` (dead-man's-switch
externo, ej. healthchecks.io; ver "Puntos delicados" para el porqué).

Toda la configuración entra por variables de entorno (`.env`), leídas solo en
`main._config_desde_env()`. Los módulos reciben todo por parámetros — no leen
`os.environ` directamente (excepto `tourplan_automation` para la ruta del
screenshot).

## Puntos delicados

- **Login limitado por licencias → sesión persistente**: Tourplan NX limita
  los usuarios logueados en simultáneo, así que NO hay que abrir un login por
  corrida. Tras el primer login exitoso se guarda el estado del navegador
  (cookie `TPT_|TourplanNX` + `localStorage`) en `data/tourplan_session.json`
  vía `storage_state` de Playwright, y las corridas siguientes lo reutilizan
  (si la sesión sigue viva, se saltea el login). Es una **SPA Angular con hash
  routing**: al entrar muestra el login un instante mientras restaura la
  sesión y luego redirige a `#/home` — por eso la sesión se detecta esperando
  la URL `#/home` (`_llego_a_home`), NO mirando si el campo de login está
  visible (daría un login "fantasma"). El token NO está en `sessionStorage`
  (que `storage_state` no persistiría). Archivo de sesión = secreto, está en
  `.gitignore`.
- **Selectores de Tourplan verificados end-to-end** (login → sesión → menú
  System/Code Setup → Exchange Rates → filtro → grilla → popup) contra la
  instancia real con el usuario `BOT`, hasta dejar el popup listo para
  guardar (sin llegar a clickear Save en una corrida real). Puntos no obvios:
  - **Casi ningún input tiene `<label>`/`id`/`aria-label`** (SPA Angular): se
    usa `get_by_placeholder` (login), clases CSS (`tpdate-*`, `tpnumber-*`)
    para fechas/montos, y texto (`get_by_text` + `force=True`, porque un
    `<div class="click-area">` superpuesto intercepta el click normal) para
    los ítems del sidebar.
  - **El usuario importa**: no cualquier usuario ve "System" en el menú de
    Code Setup — depende de permisos/rol (ver
    [[tourplan-usuario-sin-acceso-exchange-rates]]).
  - **El ícono hamburguesa** (`<img class="hamburger">`) abre/cierra el
    sidebar; no tiene rol accesible.
  - **El popup "Exchange Rate" NO usa `role="dialog"`** — es un
    `div.tpmodal-exchangerates`; no sirve `get_by_role("dialog", ...)`.
  - **"Rate Divider" es un radio implementado como `<input type="button">`**
    (no checkbox) → hace falta `.click()`, `.check()` falla. Además queda
    fuera del viewport default de Playwright (1280x720) → se agrandó a
    1600x1200 (`VIEWPORT` en `tourplan_automation.py`).
  - **"Update Inverse Rate" es un checkbox real pero oculto por CSS**
    (el ícono de al lado es lo que se ve) → ni `.check()` ni `.click()` lo
    aceptan aunque sea `force=True` ("element is not visible"); hace falta
    disparar el `.click()` nativo del DOM vía `.evaluate("el => el.click()")`.
  - **`Escape` cierra el modal completo**, no solo el datepicker interno →
    usar `Tab` para sacar el foco y cerrar el calendario sin perder los
    cambios (`_llenar_fecha`).
  - **Los inputs de fecha son enmascarados**: `.fill("")` no los limpia bien
    (puede dejar un dígito viejo pegado al valor nuevo, ej. "6/Jul/2026" en
    vez de "16/Jul/2026", con clase `tpinvalid`) → hay que limpiar con
    `Control+A` + `Delete` reales por teclado antes de tipear.
  - **La grilla usa scroll virtual**: `page.locator("table tr")` de
    Playwright solo ve las filas ya renderizadas, no todas las que existen.
    Por eso `_ubicar_fila` primero llama a `_filtrar_por_currency_from_y_to`
    (filtros reales "Currency From" + "Currency To" de la grilla, selectores
    `#exchangeRatesCurrencyFrom`/`#exchangeRatesCurrencyTo`) — sin el filtro
    de Currency To, divisas que ordenan alfabéticamente después de las ya
    renderizadas (ej. `USD` después de `MEP`) no aparecen aunque su fila
    exista. La opción del dropdown se clickea scopeada al propio combo (ej.
    `#exchangeRatesCurrencyTo .dropdown tr`, NO buscar el texto "USD" en toda
    la página: aparece también como texto plano en celdas de la grilla de
    fondo y matchea la fila equivocada).
  - **El filtro "Rate From" TAMBIÉN debe fijarse en la fecha de hoy, no
    dejarlo en su default (~1 mes atrás)**: dejar el default trae de vuelta
    TODO el historial de filas cerradas de la divisa (una por día), y con
    Currency To repetido entre varias divisas (ej. USD tiene ~30 filas
    históricas entre ARS, BLU, BRL, CLP, MEP, PRO, USD) la fila realmente
    vigente queda ambigua entre muchas candidatas — el guardado puede
    devolver 200 OK con el payload perfecto y aun así no reflejarse al
    releer, porque terminó aplicando sobre una fila vieja/equivocada.
    Filtrando **Rate From = hoy** además de Rate To = 31/Dec/2049
    (`_aplicar_filtro_auditoria`), la grilla devuelve solo un puñado de
    filas vigentes por divisa, sin ambigüedad. Instrucción confirmada por
    Eurotur tras una corrida real fallida.
  - **El guardado puede tardar más de 15s**: verificado ~17s para Currency
    To=USD (con más historial) vs. rápido para MEP. `SAVE_TIMEOUT_MS`
    (45s) es un timeout dedicado y más generoso solo para esperar a que el
    popup se cierre tras Save — usar `DEFAULT_TIMEOUT_MS` (15s) ahí hacía
    parecer que el guardado había fallado (Playwright tiraba timeout)
    cuando en realidad el POST seguía en curso del lado del servidor.
  - **`Applies To` de la fila SIEMPRE debe quedar en `APPLIES_TO_FECHA_LIMITE`
    (31/Dec/2049), nunca en la fecha de hoy**: se probó con Applies From =
    Applies To = hoy en una corrida real (con Save) y Tourplan, al guardar,
    generó sola una fila de continuación para "mañana en adelante" con la
    cotización VIEJA (no la recién cargada) — el día siguiente se veía la
    cotización de hace 2 días como "vigente" hasta la próxima corrida del
    bot. Dejando `Applies To` en la fecha límite lejana, la fila del bot
    queda abierta con el valor nuevo y esa fila de continuación errónea no
    se genera. Verificado con una corrida real de las 3 divisas.
  - Si algo deja de andar (cambio de versión de Tourplan), la forma más
    rápida de re-verificar es `playwright codegen <URL>` grabando el flujo a
    mano una vez; el navegador también guarda un screenshot en `logs/` ante
    cualquier timeout.
  - **`force=True` salta TODO el auto-wait de actionability, no solo el de
    "recibe eventos"** (detectado el 2026-09-28, en una corrida real disparada
    a mano): `_click_item_menu` necesita `force=True` porque un
    `<div class="click-area">` superpuesto intercepta el click normal, pero
    eso también salta la espera de "visible" - si el sidebar todavía está en
    plena animación de apertura (típicamente justo después de un LOGIN
    FRESCO, cuando no hay sesión guardada para reutilizar y por lo tanto no
    hubo tiempo de que la página se "asiente"), el click fallaba al toque con
    "Element is not visible" en vez de esperar a que termine la animación.
    Con sesión reutilizada (el caso normal en producción) casi nunca se nota,
    porque la página ya está asentada. Fix: esperar visibilidad
    explícitamente (`item.wait_for(state="visible")`) ANTES del click
    forzado, en vez de confiar en que `force=True` también espere.
- **Scraping robusto a rediseños**: los scrapers parsean sobre el *texto
  visible* (rótulos "Compra"/"Venta", nombre de la cotización) con regex, en
  vez de depender de clases CSS. Al modificarlos, mantener esa estrategia y el
  formato de número argentino que maneja `_parse_ar_number` ("1.527,80").
  Dos rediseños de dolarhoy.com ya rompieron el parseo en producción (ambos
  detectados el 2026-09-28, corriendo `python scrapers.py` a mano tras un
  aviso de `ScrapingError` en el log):
  - **Etiqueta "Conseguilo en:" entre el nombre y "Compra"**: las
    cotizaciones que se consiguen vía broker (MEP, CCL, Dólar Digital)
    agregaron ese texto de por medio, y el margen de tolerancia antes de
    "Compra" en el regex (`\D{0,15}?`) no lo cubría — se subió a `\D{0,40}?`
    en `get_dolar_mep`/`get_dolar_oficial`. Oficial no tiene esa etiqueta
    (no se consigue vía broker), pero se subió el margen igual ahí por si
    se agrega a futuro.
  - **Números sin coma y con punto de miles** (ej. Oficial mostrado como
    `"$1.550"` en vez de `"$1500,00"`): `_parse_ar_number` asumía que, sin
    coma, el punto era decimal (una herencia de cuando se scrapeaba BNA, que
    sí mostraba decimales así — ver el punto de abajo). Devolvía `1.55` en
    vez de `1550`. Ahora distingue por cantidad de dígitos después del
    punto: exactamente 3 dígitos = separador de miles (se elimina), otra
    cantidad = punto decimal real (se deja).
- **Fallback a dolarapi.com (agregado 2026-09-28, tras el segundo incidente
  real de scraping roto en un mes)**: `_fallback_dolarapi()` en
  [scrapers.py](scrapers.py) pega a `https://dolarapi.com/v1/dolares/{casa}`
  (`casa="oficial"` para Oficial, `casa="bolsa"` como equivalente de MEP) —
  JSON simple con `compra`/`venta` numéricos, sin HTML que un rediseño pueda
  romper. Un solo intento, sin los reintentos de `_fetch()`. A propósito
  **no se agregó el flag de "se usó fallback" al dataclass `Cotizaciones`**:
  ese dataclass se persiste tal cual (`asdict`) en
  `data/historial_cotizaciones.json` y entra en la comparación campo por
  campo de `validar_y_registrar()` — un campo extra ahí se colaría en esa
  comparación. En su lugar, `obtener_cotizaciones()` devuelve
  `ResultadoScraping` (envuelve `Cotizaciones` + `fuentes_fallback`), y
  `main.py` manda un aviso informativo y no bloqueante (reusando `_alertar`)
  cuando `fuentes_fallback` no está vacío — la carga sigue con el valor de
  respaldo, pero alguien tiene que enterarse para arreglar el parser de
  dolarhoy.com, o un fallback silencioso lo esconde para siempre. `bolsa` de
  dolarapi.com no es idéntico en metodología a "MEP" de dolarhoy.com (fuentes
  distintas); se acepta la pequeña diferencia porque `validacion.py` sigue
  validando el valor de fallback igual que cualquier otro.
- **Heartbeat externo, independiente del mail (agregado 2026-09-28)**:
  [heartbeat.py](heartbeat.py) → `enviar_heartbeat()` pinguea `HEARTBEAT_URL`
  (ej. healthchecks.io) SOLO en el camino 100% exitoso de `main()`, al final
  de todo. Existe porque el mail de `alertas.py` depende de la misma
  configuración (`.env`) que el resto del bot — el incidente del 31/08/2026
  (cron en Docker sin las variables de entorno) dejó al bot fallando en
  silencio ~40 días porque ni siquiera podía mandar el mail de alerta (el
  SMTP vivía en el mismo `.env` ausente). Un heartbeat es un canal aparte:
  si el bot no llega a pingear un día, el servicio externo nota la ausencia
  y avisa por su cuenta, sin depender de la salud del bot. Por eso NO se
  envuelve en try/except en `main.py` — `enviar_heartbeat()` ya absorbe
  cualquier error de red internamente y nunca lanza. No se pinguea en
  ningún camino de error (`ScrapingError`/`ValidacionError`/
  `TourplanAutomationError`): es justo esa ausencia la que el dead-man's-switch
  debe detectar.
- **Dólar Oficial pasó de scrapear el BNA a dolarhoy.com**: bna.com.ar era
  intermitente (bloqueaba con WAF, o cerraba la conexión sin responder y se
  recuperaba sola a los pocos segundos, sin un patrón claro de causa). Se
  unificó con la misma fuente que ya se usaba para el MEP (`get_dolar_oficial()`
  en [scrapers.py](scrapers.py)), evitando mantener dos scrapers/dos fuentes
  distintas. `_fetch()` sigue reintentando 3 veces con espera entre intentos
  antes de lanzar `ScrapingError` por si dolarhoy.com tiene un corte puntual.
- **Fechas Tourplan**: formato real `dd/Mon/yyyy` con mes en inglés abreviado
  (ej. `16/Jul/2026`), NO `dd/mm/yyyy` — `_fmt_fecha_hoy()` arma el string a
  mano (sin `strftime("%b")`, que dependería del locale del sistema).
  `APPLIES_TO_FECHA_LIMITE` (`31/Dec/2049`) se usa para el filtro "Rate To" de
  la grilla Y para el campo `Applies To` de la fila en el popup de edición
  (`Applies From` de la fila sí se setea a la fecha de hoy). Ver el punto de
  la fila de continuación arriba para el porqué de `Applies To` fija.
- **Subcode blanco del Oficial ambiguo con OTRAS divisas, no solo el
  Emisivo**: Oficial, Emisivo y varias otras filas comparten Currency
  From/To (`ARS`/`USD`) con distintos subcodes (`CEM`, `C01`, `CBD`, `CPT`,
  `CVR`, ...); Oficial se distingue solo por tener el subcode en blanco.
  Excluir subcodes conocidos uno por uno (como se hacía antes) no alcanza
  apenas aparece un subcode nuevo no contemplado — en una corrida real
  terminó editando por error la fila de subcode `CVR` en vez de la de
  Oficial. `_ubicar_fila` en [tourplan_automation.py](tourplan_automation.py)
  ahora compara el subcode de cada fila **exacto** (parseando las columnas
  de `inner_text()` por tab), no por `has_text`/substring — así una divisa
  con subcode blanco solo matchea filas realmente en blanco.

## Idioma

Código, comentarios, logs y mensajes de error están en español (rioplatense).
Mantener ese registro al modificar.
