"""Classifica o TEMA de uma sentença via Gemini.

Recebe o 1º parágrafo do relatório (o que o advogado leria para saber do que se
trata) e devolve um rótulo curto de tema — ex.: "PIS/COFINS", "IRPJ/CSLL".
Nada além do tema: a triagem é só para preencher a planilha de 3 colunas.

Confiabilidade: o free tier do Gemini limita ~5 requisições/min. Ao bater a cota
(429 RESOURCE_EXHAUSTED), respeitamos o retryDelay informado pela API e tentamos
de novo, em vez de derrubar a coleta inteira.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import ClientError

PROMPT_XML = Path(__file__).with_name("prompt.xml")

_MAX_TENTATIVAS_429 = 5


def _system_instruction() -> str:
    return PROMPT_XML.read_text(encoding="utf-8")


def _espera_do_429(err: ClientError, default: float = 40.0) -> float:
    """Extrai o retryDelay (segundos) da mensagem do 429; senão usa o default."""
    m = re.search(r"retry in ([\d.]+)s", str(err)) or re.search(r"'retryDelay': '(\d+)s'", str(err))
    try:
        return float(m.group(1)) + 2 if m else default
    except (ValueError, AttributeError):
        return default


def classificar_tema(
    texto: str,
    *,
    numero_processo: str = "",
    model: str = "gemini-2.5-flash",
    temperature: float = 0.1,
    top_p: float = 0.95,
    api_key: str | None = None,
    on_status=None,
) -> str:
    """Devolve o rótulo do tema. String vazia se não der para identificar.

    Trata 429 (cota) com espera e retry. `on_status(msg)` é callback opcional
    para avisar o painel durante a espera.
    """
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

    resp = None
    for tentativa in range(1, _MAX_TENTATIVAS_429 + 1):
        try:
            resp = client.models.generate_content(model=model, contents=entrada, config=cfg)
            break
        except ClientError as e:
            if getattr(e, "code", None) == 429 and tentativa < _MAX_TENTATIVAS_429:
                espera = _espera_do_429(e)
                if on_status:
                    on_status(f"⏳ Cota da IA atingida (free tier: 5/min). "
                              f"Aguardando {espera:.0f}s e tentando de novo...")
                time.sleep(espera)
                continue
            raise

    try:
        dados = json.loads(resp.text)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return ""
    return str(dados.get("tema_discussao") or "").strip()
