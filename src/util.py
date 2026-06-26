"""Utilitários sem dependências pesadas (importável pelo servidor)."""
from __future__ import annotations

import re

CNJ = re.compile(r"\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}")


def so_digitos(doc: str) -> str:
    """CNPJ/CPF só com dígitos, como o portal TRF4 exige."""
    return re.sub(r"\D", "", doc or "")
