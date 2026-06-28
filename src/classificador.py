"""Classifica o TEMA de uma sentença via Gemini.

Recebe o 1º parágrafo do relatório (o que o advogado leria para saber do que se
trata) e devolve um rótulo curto de tema — ex.: "PIS/COFINS", "IRPJ/CSLL".
Nada além do tema: a triagem é só para preencher a planilha de 3 colunas.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from google import genai
from google.genai import types

PROMPT_XML = Path(__file__).with_name("prompt.xml")


def _system_instruction() -> str:
    return PROMPT_XML.read_text(encoding="utf-8")


def classificar_tema(
    texto: str,
    *,
    numero_processo: str = "",
    model: str = "gemini-2.5-flash",
    temperature: float = 0.1,
    top_p: float = 0.95,
    api_key: str | None = None,
) -> str:
    """Devolve o rótulo do tema. String vazia se não der para identificar."""
    if not (texto or "").strip():
        return ""
    key = api_key or os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=key)

    cfg = types.GenerateContentConfig(
        system_instruction=_system_instruction(),
        temperature=temperature,
        top_p=top_p,
        response_mime_type="application/json",
    )
    entrada = f'numero_processo = "{numero_processo}".\nTEXTO: {texto}'
    resp = client.models.generate_content(model=model, contents=entrada, config=cfg)
    try:
        dados = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError):
        return ""
    return str(dados.get("tema_discussao") or "").strip()
