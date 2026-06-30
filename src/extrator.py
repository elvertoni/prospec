"""Texto da sentença -> trecho que revela o tema, para enviar à IA.

O coletor entrega o texto do documento (innerText do eProc). Aqui isolamos o
começo da exposição — onde a sentença diz "Trata-se de ação..." — que é o que o
advogado leria para saber do que se trata.

v2: Extração estruturada. Tenta identificar seções (relatório, fundamentação,
dispositivo) por marcadores textuais. Se conseguir, extrai cada seção
separadamente. Se não, fallback para o método original (1800 chars a partir
de 'trata-se').

Cuidado: muitas sentenças (sobretudo Mandado de Segurança) DISPENSAM o relatório
("dispenso o relatório, decido"), então ancorar na palavra "relatório" pega o
trecho errado. Ancoramos nos gatilhos de abertura do mérito.
"""
from __future__ import annotations

import re

_MAX = 1800   # chars enviados à IA no fallback (contexto suficiente, baixo custo)
_SECAO_MAX = 2500  # chars máximos por seção na extração estruturada

# Gatilhos que marcam o início da exposição do caso (ordem não importa: pega o
# de menor posição no texto).
_ABERTURA = ("trata-se", "cuida-se", "trata se", "cuida se")

# Marcadores para parse estruturado (case-insensitive, busca em texto.lower())
# Cada grupo é uma lista de strings; a primeira encontrada dispara a seção.
_MARCADORES = {
    # Início do relatório
    "inicio_relatorio": [
        "relatÓrio",
        "relatorio",
        "vistos etc",
        "vistos,",
        "vistos.",
    ],
    # Fim do relatório / início da fundamentação
    "fim_relatorio": [
        "fundamentaÇÃo",
        "fundamentacao",
        "decido",
        "passo a decidir",
        "É o relatÓrio",
        "é o relatorio",
        "passo ao exame",
    ],
    # Início do dispositivo
    "inicio_dispositivo": [
        "dispositivo",
        "ante o exposto",
        "diante do exposto",
        "isto posto",
        "posto isso",
        "do exposto",
    ],
}


def _primeira_posicao(texto_lower: str, marcadores: list[str]) -> int:
    """Retorna a menor posição (índice) onde qualquer marcador aparece,
    ou -1 se nenhum for encontrado."""
    pos = -1
    for m in marcadores:
        p = texto_lower.find(m.lower())
        if p != -1 and (pos == -1 or p < pos):
            pos = p
    return pos


def extrair_relatorio(texto_html: str, texto_raw: str | None = None) -> dict:
    """Tenta extrair seções estruturadas do texto da sentença.

    Procura por marcadores textuais comuns no eProc (RELATÓRIO,
    FUNDAMENTAÇÃO, DISPOSITIVO) para dividir o texto em seções.
    Se achar, retorna dict com cada seção. Se não, fallback para
    o método tradicional de 1800 chars a partir de 'trata-se'.

    Args:
        texto_html: Texto innerText do documento da sentença.
        texto_raw: (reservado para uso futuro) Texto alternativo.

    Returns:
        dict com chaves:
          - relatorio: str — trecho do relatório (ou fallback)
          - fundamentacao: str — trecho da fundamentação ('' se não achada)
          - dispositivo: str — trecho do dispositivo ('' se não achado)
          - tipo: str — 'completo' (3 seções), 'relatorio' (só relatório),
                  'parcial' (relatório + 1 seção), 'raw' (fallback)
    """
    texto = texto_html or texto_raw or ""
    if not texto.strip():
        return {
            "relatorio": "",
            "fundamentacao": "",
            "dispositivo": "",
            "tipo": "raw",
        }

    baixo = texto.lower()

    # 1. Tenta localizar início do relatório
    ini_rel = _primeira_posicao(baixo, _MARCADORES["inicio_relatorio"])

    # 2. Tenta localizar fim do relatório / início da fundamentação
    fim_rel = _primeira_posicao(baixo, _MARCADORES["fim_relatorio"])

    # 3. Tenta localizar início do dispositivo
    ini_disp = _primeira_posicao(baixo, _MARCADORES["inicio_dispositivo"])

    # Se não achou início do relatório, fallback
    if ini_rel == -1:
        return _fallback_trecho(texto)

    # Ajusta: fim_rel deve estar depois de ini_rel
    if fim_rel != -1 and fim_rel <= ini_rel:
        # Procura próximo marcador de fim após ini_rel
        fim_rel = _primeira_posicao(
            baixo[ini_rel + 1:], _MARCADORES["fim_relatorio"]
        )
        if fim_rel != -1:
            fim_rel += ini_rel + 1

    # Ajusta: ini_disp deve estar depois de ini_rel
    if ini_disp != -1 and ini_disp <= ini_rel:
        ini_disp = _primeira_posicao(
            baixo[ini_rel + 1:], _MARCADORES["inicio_dispositivo"]
        )
        if ini_disp != -1:
            ini_disp += ini_rel + 1

    # Se ini_disp está antes de fim_rel, ajusta fim_rel para antes de ini_disp
    if fim_rel != -1 and ini_disp != -1 and ini_disp < fim_rel:
        fim_rel = ini_disp

    # Extrai seções
    relatorio = ""
    fundamentacao = ""
    dispositivo = ""

    # Relatório: de ini_rel até fim_rel (ou ini_disp, ou fim do texto)
    if fim_rel != -1:
        rel_end = fim_rel
    elif ini_disp != -1:
        rel_end = ini_disp
    else:
        rel_end = len(texto)
    relatorio = texto[ini_rel : min(ini_rel + _SECAO_MAX, rel_end)].strip()

    # Fundamentação: de fim_rel até ini_disp (ou fim do texto)
    if fim_rel != -1:
        fund_start = fim_rel
        fund_end = ini_disp if ini_disp != -1 else len(texto)
        fundamentacao = texto[
            fund_start : min(fund_start + _SECAO_MAX, fund_end)
        ].strip()

    # Dispositivo: de ini_disp até o fim
    if ini_disp != -1:
        dispositivo = texto[
            ini_disp : min(ini_disp + _SECAO_MAX, len(texto))
        ].strip()

    # Normaliza whitespace
    relatorio = re.sub(r"[ \t]+", " ", relatorio).strip()
    fundamentacao = re.sub(r"[ \t]+", " ", fundamentacao).strip()
    dispositivo = re.sub(r"[ \t]+", " ", dispositivo).strip()

    # Determina o tipo
    if relatorio and fundamentacao and dispositivo:
        tipo = "completo"
    elif relatorio and (fundamentacao or dispositivo):
        tipo = "parcial"
    elif relatorio:
        tipo = "relatorio"
    else:
        tipo = "raw"

    return {
        "relatorio": relatorio,
        "fundamentacao": fundamentacao,
        "dispositivo": dispositivo,
        "tipo": tipo,
    }


def _fallback_trecho(texto: str) -> dict:
    """Fallback: método tradicional — 1800 chars a partir do primeiro
    gatilho de abertura ('trata-se', 'cuida-se', etc.)."""
    baixo = texto.lower()
    posicoes = [p for p in (baixo.find(g) for g in _ABERTURA) if p != -1]
    inicio = min(posicoes) if posicoes else 0
    trecho = texto[inicio : inicio + _MAX]
    trecho = re.sub(r"[ \t]+", " ", trecho).strip()
    return {
        "relatorio": trecho,
        "fundamentacao": "",
        "dispositivo": "",
        "tipo": "raw",
    }


def trecho_para_tema(texto: str) -> str:
    """Trecho a partir do início do mérito (até ~1800 chars). Vazio se não houver texto.

    v2: Usa extrair_relatorio() internamente. Se a extração estruturada
    encontrar o relatório, retorna-o; caso contrário, fallback para o
    método original (1800 chars a partir de 'trata-se').
    """
    if not (texto or "").strip():
        return ""
    resultado = extrair_relatorio(texto)
    return resultado["relatorio"]
