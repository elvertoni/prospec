"""Pontuação de prospect: quão quente é um CNPJ para o escritório.

O escritório vende TESES tributárias novas a empresas que JÁ litigam tributário.
Logo, o prospect ideal tem muitos processos tributários DE MÉRITO (mandado de
segurança, apelação, procedimento comum, remessa) — sinal de que discute a tese,
não apenas apanha em execução fiscal. Execução/cumprimento/embargos pesam pouco:
ali a empresa é ré cobrada, não autora questionando o tributo.

Entrada: list(datajud.consultar_varios(...).values()) — a lista de registros de
UM CNPJ no TRF4. Cada registro: numero, classes, assuntos, graus, tem_sentenca,
tributario.
"""
from __future__ import annotations

import re
from collections import Counter

# classe de MÉRITO: a empresa discute a tese tributária (autora/recorrente)
_MERITO = re.compile(r"mandado de seguran|apela|procedimento comum|remessa", re.I)
# classe de EXECUÇÃO/cobrança: empresa é ré; baixo valor de prospecção
_EXECUCAO = re.compile(r"execu|cumprimento|embargos", re.I)


def _eh_merito(classes: list[str]) -> bool:
    """True se alguma classe é de mérito e nenhuma a rebaixa para execução."""
    txt = " | ".join(classes)
    return bool(_MERITO.search(txt)) and not _EXECUCAO.search(txt)


def pontuar_cnpj(registros: list[dict]) -> dict:
    """Pontua um CNPJ (0..100) pela promessa como prospect tributário.

    Heurística (capada em 100):
      - n_merito_ativo  -> peso FORTE  (12 pts/processo, satura ~5 processos)
      - diversidade de temas tributários -> peso MÉDIO (8 pts/tema distinto)
      - n_tributario total -> peso LEVE (2 pts/processo, satura ~10)
    nivel: alta >=60, media 30..59, baixa <30.
    """
    n_total = len(registros)
    tributarios = [r for r in registros if r.get("tributario")]
    n_tributario = len(tributarios)

    merito_ativos = [r for r in tributarios if _eh_merito(r.get("classes", []))]
    n_merito_ativo = len(merito_ativos)

    # temas = assuntos tributários distintos, dos processos tributários,
    # ordenados do mais frequente para o menos frequente
    contagem = Counter()
    for r in tributarios:
        for a in r.get("assuntos", []):
            if a:
                contagem[a] += 1
    temas = [a for a, _ in contagem.most_common()]

    # --- score: cada componente saturado para não estourar com um só sinal ---
    s_merito = min(n_merito_ativo * 12, 60)        # forte, teto 60
    s_temas = min(len(temas) * 8, 30)              # médio, teto 30
    s_trib = min(n_tributario * 2, 20)             # leve, teto 20
    score = round(min(s_merito + s_temas + s_trib, 100.0), 1)

    if score >= 60:
        nivel = "alta"
    elif score >= 30:
        nivel = "media"
    else:
        nivel = "baixa"

    # resumo em 1 frase
    if n_tributario:
        amostra = ", ".join(temas[:3]) if temas else "temas n/d"
        resumo = (f"Empresa com {n_tributario} processo(s) tributário(s) "
                  f"({amostra}); prospect {nivel}.")
    else:
        resumo = f"Empresa sem processo tributário no TRF4; prospect {nivel}."

    return {
        "score": score,
        "nivel": nivel,
        "n_total": n_total,
        "n_tributario": n_tributario,
        "n_merito_ativo": n_merito_ativo,
        "temas": temas,
        "resumo": resumo,
    }
