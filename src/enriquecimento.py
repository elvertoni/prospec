"""Normalizacao deterministica de registros antes de gravar na planilha."""
from __future__ import annotations

from . import cnpj as cnpj_api
from .util import so_digitos

VALORES_INVALIDOS = {"", "nao_informado", "não informado", "n/d", "none", "null"}


def valor_util(v) -> bool:
    return str(v or "").strip().lower() not in VALORES_INVALIDOS


def enriquecer_registro(
    registro: dict,
    *,
    cnpj: str | None = None,
    nome_parte: str | None = None,
) -> dict:
    """Garante campos objetivos que a IA nao deve decidir sozinha.

    A regra e conservadora:
    - CNPJ vem do input da fila, nao do modelo.
    - nome_cliente vem do polo extraido; se faltar, BrasilAPI; se ainda faltar,
      mantem nome valido da IA; por ultimo grava um marcador com o CNPJ.
    """
    cnpj_num = so_digitos(cnpj or registro.get("cnpj") or "")
    if cnpj_num:
        registro["cnpj"] = cnpj_num

    nome_ia = registro.get("nome_cliente")
    nome_brasilapi = cnpj_api.razao_social(cnpj_num) if cnpj_num else None
    nome = _primeiro_util(nome_parte, nome_brasilapi, nome_ia)
    if nome:
        registro["nome_cliente"] = nome
    elif cnpj_num:
        registro["nome_cliente"] = f"CNPJ {cnpj_num}"

    return registro


def _primeiro_util(*valores) -> str | None:
    for v in valores:
        if valor_util(v):
            return str(v).strip()
    return None
