"""PDF de sentença -> texto plano (relatório + dispositivo) para a IA.

O classificador trata ruído de OCR, então aqui só extraímos e recortamos os
trechos mais úteis: o RELATÓRIO e o DISPOSITIVO (a partir de gatilhos como
"Ante o exposto"). Se nada for encontrado, devolve o texto inteiro.
"""
from __future__ import annotations

import re
from pathlib import Path

GATILHOS_DISPOSITIVO = ["ante o exposto", "isto posto", "isso posto", "julgo"]


def pdf_para_texto(caminho_pdf: str | Path) -> str:
    """Extrai todo o texto do PDF (camada de texto; sem OCR aqui)."""
    import pdfplumber  # lazy: só o modo --pdf precisa; servidor não carrega
    partes: list[str] = []
    with pdfplumber.open(str(caminho_pdf)) as pdf:
        for pagina in pdf.pages:
            partes.append(pagina.extract_text() or "")
    return "\n".join(partes).strip()


def recortar_relatorio_e_dispositivo(texto: str, max_chars: int = 12000) -> str:
    """Tenta isolar relatório + dispositivo para reduzir tokens enviados à IA.

    Heurística simples: pega do primeiro "RELATÓRIO" até o fim do dispositivo.
    Se não achar marcadores, devolve os primeiros max_chars.
    """
    if not texto:
        return ""

    baixo = texto.lower()
    inicio = baixo.find("relat")  # "relatório" / "relatorio"
    if inicio == -1:
        inicio = 0

    fim = len(texto)
    for gat in GATILHOS_DISPOSITIVO:
        pos = baixo.find(gat, inicio)
        if pos != -1:
            # do gatilho do dispositivo, leva mais ~2000 chars (texto da decisão)
            fim = min(len(texto), pos + 2000)
            break

    trecho = texto[inicio:fim].strip()
    return trecho[:max_chars] if trecho else texto[:max_chars]


def primeiro_paragrafo_relatorio(texto: str) -> str:
    """Primeiro parágrafo do relatório — o que o João lê pra saber o tema."""
    baixo = texto.lower()
    i = baixo.find("relat")
    if i == -1:
        i = 0
    resto = texto[i:]
    paragrafos = [p.strip() for p in re.split(r"\n\s*\n", resto) if p.strip()]
    return paragrafos[0] if paragrafos else resto[:600]
