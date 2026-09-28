"""
scrapers.py
-----------
Extrae las cotizaciones necesarias y calcula los 3 valores a cargar en Tourplan:

    Dolar Oficial  = Valor Venta del "Dólar Oficial" en dolarhoy.com
    Dolar Emisivo  = Dolar Oficial + 10 ARS
    Dolar MEP      = Valor Venta del "Dólar MEP" en dolarhoy.com

La página fuente es HTML de servidor (no requiere JavaScript), por lo que
alcanza con requests + BeautifulSoup. No hace falta un navegador headless
para esta parte.

`_fetch` reintenta con backoff antes de darse por vencido, por si el sitio
tarda o corta la conexión en algún request puntual.

Estrategia de parseo: en vez de depender de nombres de clases CSS (que un
rediseño del sitio puede cambiar de un día para otro), se busca sobre el
TEXTO VISIBLE de la página los rótulos "Compra"/"Venta" cerca del nombre de
cada cotización. Es más robusto ante cambios de maquetación.

Si aun así dolarhoy.com no se puede parsear (rediseño no contemplado, corte
del sitio), `obtener_cotizaciones()` usa dolarapi.com como respaldo
automático (JSON simple, sin HTML que romper) - ver `_obtener_con_fallback`.
Si dolarhoy.com y dolarapi.com fallan las dos, recién ahí se corta la carga
del día con `ScrapingError`, igual que antes de tener este respaldo.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("tourplan_fx_bot.scrapers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

DOLARHOY_URL = "https://dolarhoy.com/"

REQUEST_TIMEOUT = 20


class ScrapingError(RuntimeError):
    """Se lanza cuando no se puede extraer un valor esperado de una página."""


@dataclass
class Cotizaciones:
    dolar_mep: float
    dolar_oficial: float
    dolar_emisivo: float


@dataclass
class ResultadoScraping:
    """Envuelve `Cotizaciones` con qué fuentes tuvieron que usar el fallback
    de dolarapi.com. NO se agrega ese dato a `Cotizaciones` a propósito: ese
    dataclass se persiste tal cual (vía `asdict`) en el historial que usa
    `validacion.py` para comparar día a día, y un campo extra ahí se colaría
    en esa comparación."""

    cotizaciones: Cotizaciones
    fuentes_fallback: list[str]


def _parse_ar_number(raw: str) -> float:
    """Convierte números en formato argentino o con punto decimal a float.

    Ejemplos:
        "1.527,80" -> 1527.80   (formato argentino, coma decimal)
        "1527,80"  -> 1527.80
        "1.550"    -> 1550.0    (dolarhoy sin decimales: el punto es de miles)
        "1492.0000" -> 1492.0   (algunas tablas del BNA usan punto decimal)

    Sin coma, un punto es ambiguo (miles vs. decimal) - se distingue por la
    cantidad de dígitos que deja atrás: un punto de miles siempre agrupa de a
    3 dígitos exactos (detectado el 2026-09-28: dolarhoy pasó a mostrar el
    Oficial como "$1.550" sin coma, y esta función lo interpretaba como
    1.55); un punto decimal heredado de BNA no deja justo 3 (ej. "0000").
    """
    raw = raw.strip()
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        raw = raw.replace(".", "")
    return float(raw)


REINTENTOS = 3
ESPERA_ENTRE_REINTENTOS_SEG = 3.0


def _fetch(url: str) -> str:
    """Pide la URL con reintentos (ver nota sobre bna.com.ar en el docstring
    del módulo: se recuperó sola en pruebas manuales a los pocos segundos)."""
    ultimo_error: Exception | None = None
    for intento in range(1, REINTENTOS + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as exc:
            ultimo_error = exc
            logger.warning(
                "Intento %d/%d fallido pidiendo %s: %s", intento, REINTENTOS, url, exc
            )
            if intento < REINTENTOS:
                time.sleep(ESPERA_ENTRE_REINTENTOS_SEG)

    raise ScrapingError(
        f"No se pudo obtener {url} tras {REINTENTOS} intentos: {ultimo_error}"
    ) from ultimo_error


def get_dolar_mep(html: str | None = None) -> float:
    """Extrae el valor VENTA del recuadro "Dólar MEP" de dolarhoy.com."""
    html = html if html is not None else _fetch(DOLARHOY_URL)
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(separator=" | "))

    # Número con o sin decimales: dolarhoy.com muestra algunas cotizaciones
    # con coma decimal ("1.531,60") y otras como entero ("1450"). El bloque
    # Compra+Venta se busca anclado al principio de la ventana (re.match, no
    # re.search) para que, si el par inmediato no matchea, NO se salte al
    # bloque de la SIGUIENTE cotización que sí tenga el formato esperado.
    #
    # El primer \D admite hasta 40 caracteres (no solo 15) porque las
    # cotizaciones que se consiguen vía broker (MEP, CCL, Dólar Digital)
    # muestran una etiqueta "Conseguilo en:" entre el nombre y "Compra"
    # (detectado el 2026-09-28: dolarhoy agregó esa etiqueta y rompió el
    # parseo de MEP, que antes tenía "Compra" pegado al nombre).
    number = r"[\d\.]+(?:,\d{2})?"
    par = rf"\D{{0,40}}?Compra\D{{0,20}}?{number}\D{{0,20}}?Venta\D{{0,15}}?({number})"
    for m in re.finditer(r"D[oó]lar\s*MEP", text, re.IGNORECASE):
        window = text[m.end(): m.end() + 300]
        # Evita confundir con "Dólar MEP Cripto" u otras variantes largas
        venta_m = re.match(par, window)
        if venta_m:
            return _parse_ar_number(venta_m.group(1))

    raise ScrapingError(
        "No se pudo encontrar el valor Venta de 'Dólar MEP' en dolarhoy.com. "
        "Es probable que el sitio haya cambiado de formato; revisar manualmente."
    )


def get_dolar_oficial(html: str | None = None) -> float:
    """Extrae el valor VENTA del recuadro "Dólar Oficial" de dolarhoy.com."""
    html = html if html is not None else _fetch(DOLARHOY_URL)
    soup = BeautifulSoup(html, "html.parser")
    text = re.sub(r"\s+", " ", soup.get_text(separator=" | "))

    # Mismo margen de 40 caracteres que get_dolar_mep antes de "Compra" (ver
    # comentario ahí) - hoy el Oficial no tiene la etiqueta "Conseguilo en:"
    # de por medio, pero conviene tolerarla igual por si dolarhoy la agrega acá también.
    number = r"[\d\.]+(?:,\d{2})?"
    par = rf"\D{{0,40}}?Compra\D{{0,20}}?{number}\D{{0,20}}?Venta\D{{0,15}}?({number})"
    for m in re.finditer(r"D[oó]lar\s*Oficial", text, re.IGNORECASE):
        window = text[m.end(): m.end() + 300]
        venta_m = re.match(par, window)
        if venta_m:
            return _parse_ar_number(venta_m.group(1))

    raise ScrapingError(
        "No se pudo encontrar el valor Venta de 'Dólar Oficial' en dolarhoy.com. "
        "Es probable que el sitio haya cambiado de formato; revisar manualmente."
    )


DOLARAPI_URL_TEMPLATE = "https://dolarapi.com/v1/dolares/{casa}"


def _fallback_dolarapi(casa: str, respuesta: dict | None = None) -> float:
    """Fallback cuando dolarhoy.com no se puede parsear: dolarapi.com expone
    el mismo dato como JSON simple y numérico, sin HTML que romper con un
    rediseño. `casa` es "oficial" o "bolsa" (bolsa = equivalente de MEP en
    dolarapi.com). Un solo intento, sin los reintentos de `_fetch` - si esto
    también falla, se corta la carga del día igual que si solo existiera
    dolarhoy.com.

    Acepta `respuesta` ya obtenida (mismo patrón que el parámetro `html` de
    get_dolar_mep/get_dolar_oficial) para poder testear sin red.
    """
    if respuesta is None:
        url = DOLARAPI_URL_TEMPLATE.format(casa=casa)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            respuesta = resp.json()
        except (requests.exceptions.RequestException, ValueError) as exc:
            raise ScrapingError(f"No se pudo obtener el fallback de {url}: {exc}") from exc

    try:
        return float(respuesta["venta"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ScrapingError(
            f"Respuesta inesperada de dolarapi.com para '{casa}': {respuesta}"
        ) from exc


def _obtener_con_fallback(obtener_de_dolarhoy, casa_dolarapi: str) -> tuple[float, bool]:
    """Intenta la fuente primaria (dolarhoy.com); si falla, prueba
    dolarapi.com. Devuelve (valor, se_uso_fallback). Si fallan las dos,
    relanza un ScrapingError combinado - se sigue cortando la carga del día
    sin tocar Tourplan, como hoy."""
    try:
        return obtener_de_dolarhoy(), False
    except ScrapingError as exc_dolarhoy:
        logger.warning(
            "Falló dolarhoy.com (%s); probando dolarapi.com como respaldo...",
            exc_dolarhoy,
        )
        try:
            return _fallback_dolarapi(casa_dolarapi), True
        except ScrapingError as exc_fallback:
            raise ScrapingError(
                f"Fallaron ambas fuentes. dolarhoy.com: {exc_dolarhoy} | "
                f"dolarapi.com: {exc_fallback}"
            ) from exc_fallback


def obtener_cotizaciones() -> ResultadoScraping:
    """Punto de entrada principal: devuelve los 3 valores listos para cargar,
    con dolarapi.com como respaldo automático si dolarhoy.com no se puede
    parsear (ver `_obtener_con_fallback`)."""
    mep, fallback_mep = _obtener_con_fallback(get_dolar_mep, "bolsa")
    oficial, fallback_oficial = _obtener_con_fallback(get_dolar_oficial, "oficial")
    emisivo = round(oficial + 10, 2)

    fuentes_fallback = [
        nombre
        for nombre, usado in (("mep", fallback_mep), ("oficial", fallback_oficial))
        if usado
    ]
    if fuentes_fallback:
        logger.warning("Se usó dolarapi.com como respaldo para: %s", fuentes_fallback)

    logger.info(
        "Cotizaciones obtenidas -> MEP: %s | Oficial: %s | Emisivo: %s",
        mep, oficial, emisivo,
    )
    cot = Cotizaciones(dolar_mep=mep, dolar_oficial=oficial, dolar_emisivo=emisivo)
    return ResultadoScraping(cotizaciones=cot, fuentes_fallback=fuentes_fallback)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(obtener_cotizaciones())
