"""Texto da sentença -> trecho do RELATÓRIO que revela o tema.

O coletor entrega o texto do documento (HTML do eProc). Aqui isolamos o começo
do relatório — onde a sentença diz "Trata-se de ação..." — que é o que o
advogado leria para saber do que se trata. É esse trecho que vai para a IA.
"""
from __future__ import annotations

import re

_MAX = 1800  # chars enviados à IA: contexto suficiente, baixo custo


def trecho_para_tema(texto: str) -> str:
    """Início do relatório (até ~1800 chars). Vazio se não houver texto."""
    if not (texto or "").strip():
        return ""
    baixo = texto.lower()
    i = baixo.find("relat")  # "relatório" / "relatorio"
    if i == -1:
        i = 0
    resto = texto[i:].strip()
    # primeiro parágrafo costuma trazer "Trata-se de..."; se for curto, leva mais
    paragrafos = [p.strip() for p in re.split(r"\n\s*\n", resto) if p.strip()]
    trecho = paragrafos[0] if paragrafos else resto
    if len(trecho) < 200 and len(paragrafos) > 1:
        trecho = " ".join(paragrafos[:2])
    return trecho[:_MAX]
