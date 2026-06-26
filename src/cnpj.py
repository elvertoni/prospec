"""Resolve CNPJ → razão social via BrasilAPI (grátis, sem chave).

Dado um CNPJ (só dígitos importam), bate em GET /api/cnpj/v1/{cnpj} e devolve
os dados cadastrais públicos: razão social, nome fantasia, UF e município.

Uso no pipeline: dá nome humano ao CNPJ que entra na prospecção, sem depender
de planilha manual.

Cache em memória por processo (não repete chamada do mesmo CNPJ). A BrasilAPI
às vezes responde 429 sob carga -> retry/backoff.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

URL = "https://brasilapi.com.br/api/cnpj/v1/{cnpj}"

_cache: dict[str, dict | None] = {}  # cnpj14 -> registro (ou None p/ não achado)


def _get(cnpj14: str, tentativas: int = 4) -> dict | None:
    """Bate na BrasilAPI. Devolve o JSON cru ou None (404/erro/timeout)."""
    url = URL.format(cnpj=cnpj14)
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "joao-prospec"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limit -> espera e tenta de novo
                time.sleep(2 * (i + 1))  # backoff linear
                continue
            return None  # 404 e demais: CNPJ inexistente/inválido
        except Exception:  # noqa: BLE001  # rede fora, timeout, JSON ruim
            time.sleep(2 * (i + 1))
    return None


def consultar(cnpj: str) -> dict | None:
    """Devolve {cnpj, razao_social, nome_fantasia, uf, municipio} ou None."""
    cnpj14 = re.sub(r"\D", "", cnpj)
    if cnpj14 in _cache:
        return _cache[cnpj14]
    d = _get(cnpj14)
    reg = None
    if d and d.get("razao_social"):
        reg = {
            "cnpj": cnpj14,
            "razao_social": d.get("razao_social"),
            "nome_fantasia": d.get("nome_fantasia"),
            "uf": d.get("uf"),
            "municipio": d.get("municipio"),
        }
    _cache[cnpj14] = reg
    return reg


def razao_social(cnpj: str) -> str | None:
    """Atalho: só a razão social, ou None se não achar/erro."""
    reg = consultar(cnpj)
    return reg["razao_social"] if reg else None
