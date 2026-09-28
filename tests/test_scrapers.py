"""
Tests de los parsers de scrapers.py contra HTML capturado (sin red).

Los fixtures en tests/fixtures/ son recortes de la estructura real de
dolarhoy.com relevados con Chrome DevTools; ver el comentario en cada
fixture para el detalle de qué se capturó y cuándo.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scrapers import (
    ScrapingError,
    _parse_ar_number,
    get_dolar_mep,
    get_dolar_oficial,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _leer_fixture(nombre: str) -> str:
    return (FIXTURES_DIR / nombre).read_text(encoding="utf-8")


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
