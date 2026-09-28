"""
Tests de heartbeat.py - sin red real (requests.get mockeado).
"""
from __future__ import annotations

import pytest
import requests

import heartbeat
from heartbeat import enviar_heartbeat


def test_url_vacia_no_llama_a_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    def _requests_get_no_deberia_llamarse(*args: object, **kwargs: object) -> None:
        raise AssertionError("No debería llamarse a requests.get con HEARTBEAT_URL vacía")

    monkeypatch.setattr(heartbeat.requests, "get", _requests_get_no_deberia_llamarse)
    enviar_heartbeat("")  # no debe lanzar ni llamar a requests.get


def test_ping_exitoso_llama_a_la_url_correcta(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = []

    def _fake_get(url: str, timeout: int) -> None:
        llamadas.append((url, timeout))

    monkeypatch.setattr(heartbeat.requests, "get", _fake_get)
    enviar_heartbeat("https://hc-ping.com/fake-uuid")

    assert llamadas == [("https://hc-ping.com/fake-uuid", heartbeat.TIMEOUT_SEG)]


def test_error_de_red_no_propaga(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_get_roto(*args: object, **kwargs: object) -> None:
        raise requests.exceptions.ConnectionError("no hay red")

    monkeypatch.setattr(heartbeat.requests, "get", _fake_get_roto)
    enviar_heartbeat("https://hc-ping.com/fake-uuid")  # no debe lanzar
