"""
tourplan_automation.py
-----------------------
Automatiza la carga de los 3 tipos de cambio en Tourplan NX vía Playwright.

*** SELECTORES VERIFICADOS CONTRA LA INSTANCIA REAL ***
Todos los selectores de este módulo (login, sidebar, Code Setup, Exchange
Rates, filtro, popup de edición) fueron verificados con Playwright contra
https://tourplannx.eurotur.com.ar/tourplannx/ (usuario BOT). Es una SPA
Angular sin `<label>`/`id`/`aria-label` en la mayoría de sus inputs -> casi
todo va por placeholder o clase CSS en vez de por label/role. Si Tourplan
cambia de versión y algo deja de andar, la forma más rápida de reajustar es
`playwright codegen <URL>` grabando el flujo a mano una vez.

*** SESIÓN PERSISTENTE (login limitado por licencias) ***
Tourplan NX limita la cantidad de usuarios logueados en simultáneo, así que
NO conviene abrir un login nuevo en cada corrida (gasta una licencia y puede
dejar afuera a un usuario real). Para evitarlo se guarda el estado de sesión
del navegador (cookies + storage) en data/tourplan_session.json tras el
primer login exitoso, y las corridas siguientes lo reutilizan: si al entrar
la sesión sigue viva, se saltea el login por completo (no consume una
licencia nueva). Solo se vuelve a loguear cuando la sesión expiró.

    Pre-autenticar una vez a mano:  python tourplan_automation.py
    (loguea, guarda la sesión y sale; útil para dejarla lista sin correr
     todo el pipeline)

OJO: data/tourplan_session.json contiene cookies de autenticación -> tratarlo
como un secreto (está en .gitignore; no subirlo a ningún repo).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import date

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

logger = logging.getLogger("tourplan_fx_bot.tourplan")

# Formato real verificado contra la instancia: "dd/Mon/yyyy" con nombre de mes
# en inglés abreviado (ej. "31/Dec/2049"), NO "dd/mm/yyyy". Se usa como filtro
# "Rate To" para asegurar que la grilla muestre la fila vigente de cada divisa
# (que suele tener Applies To = esta fecha límite lejana) sin que quede fuera
# de la ventana de fechas por defecto (que es angosta, ~1 mes).
APPLIES_TO_FECHA_LIMITE = "31/Dec/2049"
DEFAULT_TIMEOUT_MS = 15_000

# El guardado de una fila puede tardar bastante más que DEFAULT_TIMEOUT_MS
# cuando la Currency To tiene mucho historial (verificado: ~17s para USD,
# ~30 filas). Timeout dedicado y generoso solo para esperar el cierre del
# popup tras Save.
SAVE_TIMEOUT_MS = 45_000

_MESES_EN = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]

# Estado de sesión del navegador (cookies + storage) para reutilizar el login
# entre corridas y no gastar una licencia concurrente cada vez. Ver docstring
# del módulo. Contiene cookies de auth -> es un secreto (está en .gitignore).
SESSION_FILE = os.path.join(os.path.dirname(__file__), "data", "tourplan_session.json")

# Tras loguearse (o restaurar la sesión), la SPA aterriza en la ruta #/home.
# Se usa para distinguir "sesión viva" de "hay que loguear".
HOME_URL_RE = re.compile(r"#/home")


class TourplanAutomationError(RuntimeError):
    """Error durante la automatización de Tourplan (para poder alertar por email)."""


@dataclass
class DivisaConfig:
    nombre: str          # Para logs/errores, ej "Dólar MEP"
    currency_from: str    # ej "ARS"
    currency_to: str      # ej "MEP" o "USD"
    subcode: str          # ej "CPT", "" (blanco), "CEM" -> se compara EXACTO en _ubicar_fila
    valor: float          # tipo de cambio a cargar


def _fmt_fecha_hoy() -> str:
    # No usar strftime("%d/%b/%Y"): el nombre del mes dependería del locale del
    # sistema. Tourplan siempre muestra el mes en inglés abreviado.
    hoy = date.today()
    return f"{hoy.day:02d}/{_MESES_EN[hoy.month - 1]}/{hoy.year}"


def _fmt_rate(valor: float) -> str:
    # Tourplan suele esperar el número con punto decimal, sin separador de miles.
    return f"{valor:.2f}"


def cargar_tipos_de_cambio(
    mep: float,
    oficial: float,
    emisivo: float,
    *,
    base_url: str,
    usuario: str,
    password: str,
    headless: bool = True,
) -> None:
    """Ejecuta el flujo completo: login -> navegación -> carga de las 3 divisas."""

    divisas = [
        DivisaConfig("Dólar MEP", "ARS", "MEP", "CPT", mep),
        # Subcode "Blanco"/Cambio BNA. _ubicar_fila compara el subcode EXACTO,
        # así que esto ya no matchea CEM (Emisivo) ni ningún otro subcode.
        DivisaConfig("Dólar Oficial", "ARS", "USD", "", oficial),
        DivisaConfig("Dólar Emisivo", "ARS", "USD", "CEM", emisivo),
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = _nuevo_context_con_sesion(browser)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)

        try:
            _asegurar_sesion(page, base_url, usuario, password)
            _guardar_sesion(context)  # persistir/refrescar cookies para la próxima corrida
            exchange_rates_page = _navegar_a_exchange_rates(context, page)
            _aplicar_filtro_auditoria(exchange_rates_page)

            for divisa in divisas:
                logger.info("Actualizando %s ...", divisa.nombre)
                _actualizar_divisa(exchange_rates_page, divisa)
                logger.info("%s actualizado OK.", divisa.nombre)

        except PlaywrightTimeoutError as exc:
            _screenshot_error(page, "timeout")
            raise TourplanAutomationError(
                f"Timeout esperando un elemento en Tourplan: {exc}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 - queremos capturar todo para alertar
            _screenshot_error(page, "error")
            raise TourplanAutomationError(f"Error durante la automatización de Tourplan: {exc}") from exc
        finally:
            context.close()
            browser.close()


def _screenshot_error(page: Page, tag: str) -> None:
    try:
        path = os.path.join(os.path.dirname(__file__), "logs", f"error_{tag}_{date.today().isoformat()}.png")
        page.screenshot(path=path, full_page=True)
        logger.error("Captura de pantalla del error guardada en %s", path)
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo guardar la captura de pantalla del error.")


# El viewport default de Playwright (1280x720) es más bajo que el popup
# "Exchange Rate" -> algunos de sus controles (Rate Divider) quedan fuera del
# viewport y ni el scroll automático ni force=True alcanzan para clickearlos
# (Playwright igual necesita coordenadas dentro del viewport). Verificado
# contra la instancia real.
VIEWPORT = {"width": 1600, "height": 1200}


def _nuevo_context_con_sesion(browser):
    """Crea el contexto del navegador reutilizando la sesión guardada si existe.

    Si el archivo de sesión está corrupto o Playwright lo rechaza, se descarta
    y se arranca con un contexto limpio (que forzará un login nuevo).
    """
    if os.path.exists(SESSION_FILE):
        try:
            logger.info("Reutilizando sesión guardada de %s", SESSION_FILE)
            return browser.new_context(storage_state=SESSION_FILE, viewport=VIEWPORT)
        except Exception:  # noqa: BLE001
            logger.warning(
                "La sesión guardada en %s no se pudo cargar; se descarta y se hará login nuevo.",
                SESSION_FILE,
            )
            try:
                os.remove(SESSION_FILE)
            except OSError:
                pass
    return browser.new_context(viewport=VIEWPORT)


def _llego_a_home(page: Page, timeout_ms: int) -> bool:
    """True si la SPA aterriza en #/home dentro del timeout.

    Es la señal de "sesión viva". OJO con el timing: al entrar, la SPA Angular
    muestra el login un instante mientras restaura la sesión guardada y recién
    después redirige a #/home. Por eso NO alcanza con mirar si el campo de
    login está visible (daría un login "fantasma" y gastaríamos una licencia de
    gusto): hay que esperar a ver a dónde estabiliza la navegación."""
    try:
        page.wait_for_url(HOME_URL_RE, timeout=timeout_ms)
        return True
    except PlaywrightTimeoutError:
        return False


def _asegurar_sesion(page: Page, base_url: str, usuario: str, password: str) -> None:
    """Entra a la app y hace login SOLO si la sesión reutilizada no sigue viva.

    Reutilizar la sesión evita gastar una licencia concurrente de Tourplan en
    cada corrida (ver docstring del módulo)."""
    page.goto(base_url)
    page.wait_for_load_state("networkidle")

    # Si la sesión sigue viva, la SPA restaura y redirige a #/home en pocos seg.
    if _llego_a_home(page, timeout_ms=8_000):
        logger.info("Sesión reutilizada; se omite el login (no consume una licencia nueva).")
        return

    logger.info("No hay sesión válida; iniciando login en Tourplan NX...")
    _login(page, usuario, password)
    if not _llego_a_home(page, timeout_ms=DEFAULT_TIMEOUT_MS):
        raise TourplanAutomationError(
            "El login no llegó al dashboard (#/home). Puede ser credenciales "
            "incorrectas, un usuario ya logueado (límite de licencias) o un "
            "cambio en la pantalla de login."
        )


def _guardar_sesion(context) -> None:
    """Persiste cookies + storage del contexto para reutilizarlos en la próxima corrida."""
    os.makedirs(os.path.dirname(SESSION_FILE), exist_ok=True)
    context.storage_state(path=SESSION_FILE)
    logger.info("Sesión guardada en %s (se reutilizará en la próxima corrida).", SESSION_FILE)


def _login(page: Page, usuario: str, password: str) -> None:
    """Completa el formulario de login. Asume que la página YA está en la
    pantalla de login (la navegación la hace el llamador)."""
    # Los campos de login son <input placeholder="Username"/"Password">, sin
    # <label>, id ni name -> no sirve get_by_label, hay que ir por placeholder
    # (verificado contra la instancia real).
    page.get_by_placeholder("Username").fill(usuario)
    page.get_by_placeholder("Password").fill(password)
    page.get_by_role("button", name="Login").click()
    page.wait_for_load_state("networkidle")


def _abrir_menu(page: Page) -> None:
    # El ícono hamburguesa (<img class="hamburger">) abre el sidebar; no tiene
    # rol accesible (verificado contra la instancia real).
    page.locator("img.hamburger").click()


def _click_item_menu(page: Page, texto: str) -> None:
    # Los ítems del sidebar son <label> dentro de .menu-heading; un
    # <div class="click-area"> superpuesto intercepta el click normal, por
    # eso hace falta force=True (verificado contra la instancia real).
    #
    # force=True salta TODO el auto-wait de actionability de Playwright, no
    # solo el de "recibe eventos" - incluye el de "visible". Si el sidebar
    # todavía está en plena animación de apertura (típicamente justo después
    # de un login fresco, cuando no hay sesión guardada para reutilizar), el
    # click fallaba al toque con "Element is not visible" en vez de esperar
    # a que termine (detectado el 2026-09-28: falló una vez tras un login
    # fresco, anduvo bien al reintentar con la sesión ya guardada). Por eso
    # se espera visibilidad explícitamente ANTES del click forzado.
    item = page.get_by_text(re.compile(rf"^{re.escape(texto)}$", re.IGNORECASE)).first
    item.wait_for(state="visible")
    item.click(force=True)


def _navegar_a_exchange_rates(context, page: Page) -> Page:
    logger.info("Navegando: System > Code Setup > System > Exchange Rates ...")

    _abrir_menu(page)
    _click_item_menu(page, "System")

    # "Code Setup" abre una pestaña nueva -> esperamos el evento de nueva página.
    with context.expect_page() as new_page_info:
        _click_item_menu(page, "Code Setup")
    code_setup_page = new_page_info.value
    code_setup_page.wait_for_load_state("networkidle")

    # Dentro de la pestaña nueva: menú -> System -> Exchange Rates
    _abrir_menu(code_setup_page)
    _click_item_menu(code_setup_page, "System")
    _click_item_menu(code_setup_page, "Exchange Rates")
    code_setup_page.wait_for_load_state("networkidle")

    return code_setup_page


def _llenar_fecha(page: Page, campo, valor: str) -> None:
    """Llena un input de fecha tipo Angular (con datepicker propio).

    fill("") NO limpia bien este input enmascarado -> a veces deja un dígito
    viejo pegado con el valor nuevo (ej. quedó "6/Jul/2026" en vez de
    "16/Jul/2026", con clase tpinvalid). Hay que limpiar con selección +
    Delete real por teclado antes de tipear. Y Tab (NO Escape) para sacar el
    foco y cerrar el calendario: Escape cierra el MODAL completo (Exchange
    Rate), no solo el datepicker, perdiendo los cambios. Todo verificado
    contra la instancia real. La espera final es necesaria: al cambiar una
    fecha, Angular revalida y re-renderiza brevemente los otros campos de
    fecha del mismo formulario (ej. Applies To queda "invisible" un instante
    si su valor anterior quedó fuera de rango) -> sin esta espera, el próximo
    campo puede fallar con "element is not visible"."""
    campo.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    campo.type(valor)
    page.keyboard.press("Tab")
    page.wait_for_timeout(500)


def _aplicar_filtro_auditoria(page: Page) -> None:
    # Rate From = HOY (no el default ~1 mes atrás): con el default, la grilla
    # trae de vuelta TODO el historial de filas cerradas de cada divisa
    # (una por día), y la fila realmente vigente queda ambigua entre varias
    # candidatas -> el guardado puede terminar aplicando sobre la fila
    # equivocada aunque el request/response parezcan correctos. Filtrando
    # Rate From = hoy, la grilla devuelve solo las pocas filas vigentes
    # (idealmente una por divisa), sin ambigüedad. Instrucción confirmada
    # contra la instancia real.
    hoy = _fmt_fecha_hoy()
    logger.info("Aplicando filtro Rate From = %s, Rate To = %s ...", hoy, APPLIES_TO_FECHA_LIMITE)
    # Los campos de fecha no tienen <label>/id -> no sirve get_by_label
    # (mismo patrón que los campos de login). Selectores reales por clase.
    _llenar_fecha(page, page.locator("input.tpdate-exchangeratesdatefrom"), hoy)
    _llenar_fecha(page, page.locator("input.tpdate-exchangeratesdateto"), APPLIES_TO_FECHA_LIMITE)
    page.get_by_role("button", name="Filter").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)  # el grid tarda un instante en re-renderizar tras el filtro


def _elegir_combo(page: Page, combo_id: str, valor: str) -> None:
    """Tipea y selecciona una opción en un combo tipo tp-dropdown del filtro
    (ej. #exchangeRatesCurrencyFrom, #exchangeRatesCurrencyTo)."""
    combo = page.locator(f"#{combo_id} input")
    combo.click()
    page.keyboard.press("Control+A")
    page.keyboard.press("Delete")
    combo.type(valor)
    page.wait_for_timeout(800)
    # Opción del dropdown scopeada al propio combo -- NO usar get_by_text
    # sobre toda la página: el código de la divisa también aparece como
    # texto plano en celdas de la grilla de fondo y matchea mal.
    page.locator(f"#{combo_id} .dropdown tr").first.click(force=True)
    page.wait_for_timeout(300)


def _filtrar_por_currency_from_y_to(page: Page, currency_from: str, currency_to: str) -> None:
    """Filtra la grilla por Currency From Y Currency To antes de buscar una fila.

    Currency To sola no alcanza para desambiguar: varias divisas de origen
    distintas (ARS, BLU, BRL, CLP, MEP, PRO, USD...) apuntan al mismo
    Currency To (ej. USD), así que sin fijar también Currency From=ARS el
    guardado no identifica la fila correcta -- devuelve 200 OK pero el
    cambio no persiste (verificado contra la instancia real: request y
    respuesta correctos, pero la fila no cambiaba al releer).

    Además, la grilla usa scroll virtual: `table tr` de Playwright solo ve
    las filas ya renderizadas (orden alfabético por Currency To). Sin este
    filtro, una divisa que ordena después de las ya renderizadas (ej. USD,
    después de MEP) puede no aparecer aunque su fila exista de verdad ->
    "No se encontró ninguna fila" engañoso.
    """
    _elegir_combo(page, "exchangeRatesCurrencyFrom", currency_from)
    _elegir_combo(page, "exchangeRatesCurrencyTo", currency_to)
    page.get_by_role("button", name="Filter").click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)


def _ubicar_fila(page: Page, divisa: DivisaConfig):
    """Ubica la última línea vigente para la combinación Currency From/To/Subcode.

    El subcode se compara EXACTO (no por substring): con subcode en blanco,
    `has_text` no sirve para filtrar (no hay nada que buscar) y excluir solo
    los subcodes de otras divisas conocidas (`subcodes_excluir`) no alcanza
    si existen además OTROS subcodes sin relación (ej. C01, CBD, CVR) que
    también matchean Currency From/To -> se termina editando una fila
    cualquiera de esas por error. Verificado contra la instancia real: así
    se editó "CVR" en vez de la fila de subcode blanco del Oficial.
    """
    _filtrar_por_currency_from_y_to(page, divisa.currency_from, divisa.currency_to)
    # AJUSTAR: esta es la parte más específica de tu grilla. La estrategia genérica
    # es filtrar filas de la tabla por texto visible (Currency From, Currency To)
    # y despué comparar el subcode exacto de cada fila candidata.
    candidatas = page.locator("table tr").filter(
        has_text=divisa.currency_from
    ).filter(has_text=divisa.currency_to)

    textos = candidatas.all_inner_texts()
    indices_validos = []
    for i, texto in enumerate(textos):
        columnas = texto.split("\t")
        # columnas: ['', Currency From, Currency To, Subcode, Applies From, ...]
        subcode_fila = columnas[3].strip() if len(columnas) > 3 else ""
        if subcode_fila == divisa.subcode:
            indices_validos.append(i)

    if not indices_validos:
        raise TourplanAutomationError(
            f"No se encontró ninguna fila para {divisa.nombre} "
            f"(Currency From={divisa.currency_from}, Currency To={divisa.currency_to}, "
            f"Subcode={divisa.subcode or '(blanco)'}). Revisar el filtro Rate To y la grilla."
        )
    return candidatas.nth(indices_validos[-1])  # última fila = la más vigente


def _actualizar_divisa(page: Page, divisa: DivisaConfig) -> None:
    fila = _ubicar_fila(page, divisa)
    fila.click()

    # El popup no usa role="dialog" -> selector real verificado por clase.
    popup = page.locator(".tpmodal-exchangerates")
    popup.wait_for()

    # "Applies From"/"Applies To"/"Exchange Rate" son componentes Angular
    # (tp-date/tp-number) cuyo <label for=...> apunta al wrapper del
    # componente, no al <input> real -> no sirve get_by_label, hay que ir por
    # clase (verificado contra la instancia real).
    #
    # Applies To = APPLIES_TO_FECHA_LIMITE (NO la fecha de hoy): se probó con
    # Applies From = Applies To = hoy en una corrida real y Tourplan, al
    # guardar, autogeneró una fila de continuación para el día siguiente en
    # adelante usando la cotización VIEJA (no la recién cargada) -> al otro
    # día se ve la cotización de hace 2 días como "vigente" hasta que el bot
    # vuelva a correr. Dejando Applies To en la fecha límite lejana, la fila
    # queda abierta con el valor nuevo y no se genera esa continuación
    # errónea. Verificado contra la instancia real.
    _llenar_fecha(page, popup.locator("input.tpdate-exchangeratesdatefrom"), _fmt_fecha_hoy())
    _llenar_fecha(page, popup.locator("input.tpdate-exchangeratesdateto"), APPLIES_TO_FECHA_LIMITE)
    popup.locator("input.tpnumber-exchangeratesrate").fill(_fmt_rate(divisa.valor))

    # "Rate Divider" es un radio implementado como <input type="button">
    # (no checkbox) -> .check() falla, hay que hacer .click(). "Update Inverse
    # Rate" es un <input type=checkbox> real pero visualmente oculto por CSS
    # (el ícono estilizado de al lado es lo que se ve) -> ni .check() ni
    # .click(), aun con force=True, lo aceptan ("element is not visible");
    # hace falta disparar el .click() nativo del DOM directamente. Ambos
    # verificados contra la instancia real.
    popup.get_by_label("Rate Divider").click(force=True)
    popup.get_by_label("Update Inverse Rate").evaluate("el => el.click()")

    # El guardado en sí (POST a UpdateCurrencyExchangeRates) puede tardar
    # mucho más que DEFAULT_TIMEOUT_MS: verificado contra la instancia real
    # que para Currency To=USD (~30 filas de historial) tardó ~17s, mientras
    # que para MEP (~21 filas) es rápido. Sin un timeout más largo acá, el
    # popup "Saving..." se corta como timeout de Playwright ANTES de que el
    # request realmente termine -> parece que falló pero en realidad el
    # guardado sigue en curso del lado del servidor.
    popup.get_by_role("button", name="Save").click()
    popup.wait_for(state="hidden", timeout=SAVE_TIMEOUT_MS)
    page.wait_for_load_state("networkidle")


def iniciar_sesion_y_guardar(
    *,
    base_url: str,
    usuario: str,
    password: str,
    headless: bool = True,
) -> None:
    """Loguea (si hace falta) y persiste la sesión, sin tocar los tipos de cambio.

    Sirve para pre-autenticar una vez a mano y dejar data/tourplan_session.json
    listo, de modo que las corridas del pipeline reutilicen esa sesión."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = _nuevo_context_con_sesion(browser)
        page = context.new_page()
        page.set_default_timeout(DEFAULT_TIMEOUT_MS)
        try:
            _asegurar_sesion(page, base_url, usuario, password)
            _guardar_sesion(context)
        finally:
            context.close()
            browser.close()


if __name__ == "__main__":
    # Uso: python tourplan_automation.py
    # Loguea una vez y guarda la sesión (ver docstring del módulo). Lee las
    # credenciales de .env igual que main.py.
    from dotenv import load_dotenv

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    iniciar_sesion_y_guardar(
        base_url=os.environ["TOURPLAN_URL"],
        usuario=os.environ["TOURPLAN_USER"],
        password=os.environ["TOURPLAN_PASSWORD"],
        headless=os.environ.get("HEADLESS", "true").lower() != "false",
    )
