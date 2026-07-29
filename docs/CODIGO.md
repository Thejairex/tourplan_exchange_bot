# Documentación de código — tourplan_fx_bot

Guía técnica para quien vaya a mantener o modificar este proyecto. Para una
descripción de qué hace el bot desde el punto de vista de negocio, ver
[FUNCIONALIDAD.md](FUNCIONALIDAD.md). Para detalles muy finos de selectores e
incidentes ya resueltos, ver el `CLAUDE.md` de la raíz del repo.

## Flujo general

`main.py` orquesta un pipeline lineal de 4 pasos. Cada paso vive en su propio
módulo y define su propia excepción. Si un paso falla, `main()` corta
inmediatamente, envía un email de alerta y devuelve código de salida `1` —
nunca sigue al paso siguiente con datos parciales o inválidos.

```
main.py
 ├─ 1. scrapers.obtener_cotizaciones()        -> ScrapingError
 ├─ 2. validacion.validar_y_registrar()       -> ValidacionError
 ├─ 3. tourplan_automation.cargar_tipos_de_cambio() -> TourplanAutomationError
 └─ 4. alertas.enviar_alerta()  (solo si algo de 1-3 falló)
```

El orden importa: la validación (paso 2) corre siempre **antes** de tocar
Tourplan (paso 3), para no cargar nunca un valor sospechoso.

## Módulos

### `scrapers.py`

- **Responsabilidad**: obtener las 3 cotizaciones a cargar.
- **Función pública**: `obtener_cotizaciones() -> Cotizaciones` (dataclass
  con `dolar_mep`, `dolar_oficial`, `dolar_emisivo`).
- **Excepción**: `ScrapingError`.
- Usa `requests` + BeautifulSoup contra `dolarhoy.com` (HTML de servidor, no
  hace falta navegador). `_fetch()` reintenta 3 veces con 3s de espera entre
  intentos antes de lanzar `ScrapingError`.
- `dolar_emisivo` no se scrapea: se calcula como `dolar_oficial + 10`.
- **Parseo robusto a rediseños**: en vez de depender de clases CSS, busca
  sobre el texto visible los rótulos "Compra"/"Venta" cerca del nombre de
  cada cotización (regex ancladas con `re.match`, no `re.search`, para no
  saltar por error al bloque de la cotización siguiente). `_parse_ar_number`
  entiende tanto formato argentino ("1.527,80") como con punto decimal.
- Correr aislado: `python scrapers.py` (imprime las 3 cotizaciones, no toca
  Tourplan).

### `validacion.py`

- **Responsabilidad**: evitar cargar un valor disparatado por un error de
  scraping, comparando contra el último valor cargado.
- **Función pública**: `validar_y_registrar(cot: Cotizaciones, umbral_pct: float = 15.0) -> None`.
- **Excepción**: `ValidacionError`.
- Historial persistido en `data/historial_cotizaciones.json` (clave
  `"ultimo"` con el último valor + fecha, y `"historico"` con la lista
  completa). Si el JSON está corrupto, se loguea un warning y se reinicia
  (no rompe el proceso).
- Si algún campo de `Cotizaciones` varía más del `umbral_pct` respecto del
  último valor registrado, lanza `ValidacionError` **sin escribir** el nuevo
  valor al historial. La primera corrida (sin historial previo) nunca falla.
- Si pasa la validación, el valor nuevo queda guardado como referencia para
  la corrida del día siguiente — por eso una variación real de mercado
  aceptada manualmente "resetea" el umbral a partir del otro día.

### `tourplan_automation.py`

- **Responsabilidad**: cargar las 3 divisas en Tourplan NX vía Playwright
  (SPA Angular, sin API pública).
- **Función pública principal**: `cargar_tipos_de_cambio(mep, oficial, emisivo, *, base_url, usuario, password, headless=True) -> None`.
- **Función auxiliar**: `iniciar_sesion_y_guardar(*, base_url, usuario, password, headless=True) -> None` — loguea y persiste sesión sin tocar tipos de cambio; pensada para correr a mano (`python tourplan_automation.py`).
- **Excepción**: `TourplanAutomationError`. Ante timeout o cualquier otro
  error, guarda un screenshot en `logs/error_<tag>_<fecha>.png` antes de
  relanzar.
- **Sesión persistente** (`data/tourplan_session.json`): Tourplan limita
  usuarios logueados en simultáneo, así que el bot reutiliza sesión entre
  corridas en vez de loguear cada vez. Se detecta sesión viva esperando que
  la URL llegue a `#/home` (`_llego_a_home`), no mirando si el form de login
  está visible (daría un "login fantasma"). Archivo con cookies de auth:
  está en `.gitignore`, tratar como secreto.
- **`DivisaConfig`** describe cada fila a editar: `currency_from`,
  `currency_to`, `subcode` (comparado **exacto**, no por substring — varias
  filas comparten Currency From/To y solo se distinguen por subcode) y
  `valor`.
- **Filtros de auditoria antes de tocar la grilla**: `Rate From = hoy` y
  `Rate To = 31/Dec/2049` (`APPLIES_TO_FECHA_LIMITE`). Sin fijar `Rate From`
  en hoy, la grilla trae todo el historial de filas cerradas y la fila
  vigente queda ambigua entre muchas candidatas.
- **En el popup de edición** (`.tpmodal-exchangerates`, no usa
  `role="dialog"`): `Applies From` = hoy, `Applies To` = fecha límite lejana
  (NUNCA hoy — dejarlo en hoy hace que Tourplan autogenere al guardar una
  fila de continuación con la cotización vieja). `Rate Divider` es un
  `<input type="button">` (usar `.click()`, no `.check()`). `Update Inverse
  Rate` es un checkbox real pero oculto por CSS (hace falta
  `.evaluate("el => el.click()")`).
- **Timeouts**: `DEFAULT_TIMEOUT_MS` (15s) para la mayoría de las esperas;
  `SAVE_TIMEOUT_MS` (45s) dedicado solo a esperar que se cierre el popup
  tras Save, porque el guardado puede tardar ~17s en divisas con mucho
  historial (ej. USD).
- Si algo deja de andar por un cambio de versión de Tourplan, la forma más
  rápida de reajustar selectores es `playwright codegen <URL>`.

### `alertas.py`

- **Responsabilidad**: avisar por email cuando falla cualquier paso.
- **Función pública**: `enviar_alerta(asunto, cuerpo, *, smtp_host, smtp_port, smtp_user, smtp_password, destinatario, usar_tls=True) -> None`.
- No define excepción propia: si falta configuración SMTP (`smtp_host` o
  `destinatario` vacíos), solo loguea un warning y no envía nada — **nunca
  rompe el proceso principal**. Si el envío en sí falla (credenciales,
  conexión), lo captura y loguea con `logger.exception`, tampoco propaga.

### `main.py`

- Arma logging (archivo con rotación diaria en `logs/`, 90 días de
  retención, + consola) y lee configuración de `.env` una única vez
  (`_config_desde_env`) — los demás módulos reciben todo por parámetros, no
  leen `os.environ` directamente (excepción: `tourplan_automation` para la
  ruta del screenshot).
- Variables de entorno leídas (ver `.env.example` para la lista completa
  con ejemplos):

  | Variable | Uso | Obligatoria |
  |---|---|---|
  | `TOURPLAN_URL`, `TOURPLAN_USER`, `TOURPLAN_PASSWORD` | acceso a Tourplan NX | sí |
  | `HEADLESS` | correr Playwright con/sin ventana (`true` por defecto) | no |
  | `UMBRAL_VARIACION_PCT` | umbral de validación (`15` por defecto) | no |
  | `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD` | envío de alertas | no (sin esto, no se envían alertas) |
  | `ALERTA_EMAIL_TO` | destinatario de las alertas | no (ídem) |

- Si faltan las variables obligatorias, ni siquiera intenta mandar alerta
  (no hay SMTP configurado todavía en ese punto): solo loguea el error y
  corta con código 1.

## Correr y depurar

```bash
# Setup
python3 -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env   # completar credenciales

# Pipeline completo (lo que ejecuta cron)
python main.py

# Solo scraping (no toca Tourplan)
python scrapers.py

# Pre-autenticar y dejar la sesión guardada, sin correr el pipeline
python tourplan_automation.py

# Tests (parsers de scrapers.py contra fixtures HTML; no requieren red ni Playwright)
pip install -r requirements-dev.txt
pytest -v
```

Ante un fallo en producción:

1. Revisar `logs/tourplan_fx_bot.log` (rotación diaria, 90 días).
2. Si el fallo fue en la automatización de Tourplan, buscar el screenshot en
   `logs/error_<tag>_<fecha>.png` — muestra el estado exacto de la pantalla
   al momento del error.
3. Para reproducir a mano con navegador visible: `HEADLESS=false` en `.env`
   y correr `python main.py` o `python tourplan_automation.py`.
4. Si el problema es un selector roto por un cambio de versión de Tourplan,
   grabar el flujo de nuevo con `playwright codegen <URL>` y ajustar
   `tourplan_automation.py`.
