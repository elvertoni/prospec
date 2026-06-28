"""Texto da sentença -> trecho que revela o tema, para enviar à IA.

O coletor entrega o texto do documento (HTML do eProc). Aqui isolamos o começo
da exposição — onde a sentença diz "Trata-se de ação..." — que é o que o
advogado leria para saber do que se trata.

Cuidado: muitas sentenças (sobretudo Mandado de Segurança) DISPENSAM o relatório
("dispenso o relatório, decido"), então ancorar na palavra "relatório" pega o
trecho errado. Ancoramos nos gatilhos de abertura do mérito.
"""
from __future__ import annotations

import re

_MAX = 1800  # chars enviados à IA: contexto suficiente, baixo custo

# Gatilhos que marcam o início da exposição do caso (ordem não importa: pega o
# de menor posição no texto).
_ABERTURA = ("trata-se", "cuida-se", "trata se", "cuida se")


def trecho_para_tema(texto: str) -> str:
    """Trecho a partir do início do mérito (até ~1800 chars). Vazio se não houver texto."""
    if not (texto or "").strip():
        return ""
    baixo = texto.lower()

    # menor posição entre os gatilhos de abertura
    posicoes = [p for p in (baixo.find(g) for g in _ABERTURA) if p != -1]
    inicio = min(posicoes) if posicoes else 0

    trecho = texto[inicio:inicio + _MAX]
    return re.sub(r"[ \t]+", " ", trecho).strip()
