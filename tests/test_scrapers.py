"""
Tests de los parsers de scrapers.py contra HTML capturado (sin red).

Los fixtures HTML en tests/fixtures/ son recortes de la estructura real de
dolarhoy.com relevados con Chrome DevTools; ver el comentario en cada
fixture para el detalle de qué se capturó y cuándo. Los fixtures JSON
(dolarapi_*.json) son ejemplos de la respuesta real de dolarapi.com, la
fuente de respaldo cuando dolarhoy.com no se puede parsear.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import scrapers
from scrapers import (
    ScrapingError,
    _fallback_dolarapi,
    _parse_ar_number,
    get_dolar_mep,
    get_dolar_oficial,
    obtener_cotizaciones,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _leer_fixture(nombre: str) -> str:
    return (FIXTURES_DIR / nombre).read_text(encoding="utf-8")


def _leer_fixture_json(nombre: str) -> dict:
    return json.loads(_leer_fixture(nombre))


@pytest.mark.parametrize(
    "raw, esperado",
    [
        ("1.527,80", 1527.80),
        ("1527,80", 1527.80),
        ("1492.0000", 1492.0),
        ("1460,00", 1460.0),
        ("1.550", 1550.0),
        ("1.500", 1500.0),
        ("12.345.678", 12345678.0),
    ],
)
def test_parse_ar_number(raw: str, esperado: float) -> None:
    assert _parse_ar_number(raw) == esperado


def test_get_dolar_mep_devuelve_valor_venta() -> None:
    html = _leer_fixture("dolarhoy.html")
    assert get_dolar_mep(html) == 1531.60


def test_get_dolar_mep_sin_match_lanza_scraping_error() -> None:
    with pytest.raises(ScrapingError):
        get_dolar_mep("<html><body>Sin cotizaciones acá</body></html>")


def test_get_dolar_oficial_devuelve_valor_venta() -> None:
    html = _leer_fixture("dolarhoy.html")
    assert get_dolar_oficial(html) == 1550.00


def test_get_dolar_oficial_sin_match_lanza_scraping_error() -> None:
    with pytest.raises(ScrapingError):
        get_dolar_oficial("<html><body>Sin cotizaciones acá</body></html>")


@pytest.mark.parametrize(
    "fixture, casa, esperado",
    [
        ("dolarapi_oficial.json", "oficial", 1550.0),
        ("dolarapi_bolsa.json", "bolsa", 1550.6),
    ],
)
def test_fallback_dolarapi_devuelve_valor_venta(fixture: str, casa: str, esperado: float) -> None:
    respuesta = _leer_fixture_json(fixture)
    assert _fallback_dolarapi(casa, respuesta) == esperado


def test_fallback_dolarapi_respuesta_invalida_lanza_scraping_error() -> None:
    with pytest.raises(ScrapingError):
        _fallback_dolarapi("oficial", {"moneda": "USD"})  # sin "venta"


def test_obtener_cotizaciones_usa_fallback_si_falla_dolarhoy(monkeypatch: pytest.MonkeyPatch) -> None:
    def _mep_roto() -> float:
        raise ScrapingError("dolarhoy.com cambió de formato")

    monkeypatch.setattr(scrapers, "get_dolar_mep", _mep_roto)
    monkeypatch.setattr(scrapers, "get_dolar_oficial", lambda: 1550.0)
    monkeypatch.setattr(
        scrapers, "_fallback_dolarapi", lambda casa: 1550.6 if casa == "bolsa" else 1550.0
    )

    resultado = obtener_cotizaciones()

    assert resultado.cotizaciones.dolar_mep == 1550.6
    assert resultado.cotizaciones.dolar_oficial == 1550.0
    assert resultado.fuentes_fallback == ["mep"]


def test_obtener_cotizaciones_lanza_si_fallan_ambas_fuentes(monkeypatch: pytest.MonkeyPatch) -> None:
    def _roto() -> float:
        raise ScrapingError("dolarhoy.com caído")

    def _fallback_roto(casa: str) -> float:
        raise ScrapingError(f"dolarapi.com también caído para {casa}")

    monkeypatch.setattr(scrapers, "get_dolar_mep", _roto)
    monkeypatch.setattr(scrapers, "get_dolar_oficial", lambda: 1550.0)
    monkeypatch.setattr(scrapers, "_fallback_dolarapi", _fallback_roto)

    with pytest.raises(ScrapingError):
        obtener_cotizaciones()
