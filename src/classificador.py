"""Classifica o TEMA de uma sentença via LLM.

Multi-modelo: se o `.env` definir LLM_BASE_URL + LLM_API_KEY, usa qualquer API
compatível com OpenAI (DeepSeek, OpenCode Zen, OpenRouter, etc.); senão, cai no
Gemini (google-genai). O modelo devolve um rótulo curto de tema — ex.:
"PIS/COFINS", "IRPJ/CSLL". Nada além do tema (planilha de 3 colunas).

Confiabilidade: ao bater cota (429), espera e tenta de novo, em vez de derrubar
a coleta. Parse tolerante: aceita JSON puro ou texto com o JSON embutido.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

PROMPT_XML = Path(__file__).with_name("prompt.xml")

_MAX_TENTATIVAS = 5


def _system_instruction() -> str:
    return PROMPT_XML.read_text(encoding="utf-8")


def _espera_429(msg: str, default: float = 30.0) -> float:
    m = re.search(r"retry in ([\d.]+)s", msg) or re.search(r"'retryDelay': '(\d+)s'", msg)
    try:
        return float(m.group(1)) + 2 if m else default
    except (ValueError, AttributeError):
        return default


def _extrair_tema(raw: str | None) -> str:
    """Extrai tema_discussao de uma resposta que pode vir como JSON puro,
    JSON dentro de ```...``` ou texto com o objeto embutido."""
    if not raw:
        return ""
    txt = raw.strip().strip("`")
    txt = re.sub(r"^json\s*", "", txt, flags=re.I)
    try:
        return str(json.loads(txt).get("tema_discussao") or "").strip()
    except (json.JSONDecodeError, AttributeError, TypeError):
        pass
    m = re.search(r'"tema_discussao"\s*:\s*"([^"]+)"', raw)
    return m.group(1).strip() if m else ""


def _entrada(numero_processo: str, texto: str) -> str:
    return f'numero_processo = "{numero_processo}".\nTEXTO: {texto}'


def _classificar_openai(texto, numero_processo, model, temperature, on_status) -> str:
    from openai import OpenAI
    from openai import APIStatusError

    client = OpenAI(
        base_url=os.environ["LLM_BASE_URL"],
        api_key=os.environ["LLM_API_KEY"],
        timeout=60,
    )
    mensagens = [
        {"role": "system", "content": _system_instruction()},
        {"role": "user", "content": _entrada(numero_processo, texto)},
    ]
    for tentativa in range(1, _MAX_TENTATIVAS + 1):
        try:
            resp = client.chat.completions.create(
                model=model, messages=mensagens, temperature=temperature,
            )
            return _extrair_tema(resp.choices[0].message.content)
        except APIStatusError as e:
            if e.status_code == 429 and tentativa < _MAX_TENTATIVAS:
                espera = _espera_429(str(e))
                if on_status:
                    on_status(f"⏳ Cota da IA atingida; aguardando {espera:.0f}s...")
                time.sleep(espera)
                continue
            raise
    return ""


def _classificar_gemini(texto, numero_processo, model, temperature, top_p, api_key, on_status) -> str:
    from google import genai
    from google.genai import types
    from google.genai.errors import ClientError

    client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
    cfg = types.GenerateContentConfig(
        system_instruction=_system_instruction(),
        temperature=temperature, top_p=top_p,
        response_mime_type="application/json",
    )
    for tentativa in range(1, _MAX_TENTATIVAS + 1):
        try:
            resp = client.models.generate_content(
                model=model, contents=_entrada(numero_processo, texto), config=cfg)
            return _extrair_tema(resp.text)
        except ClientError as e:
            if getattr(e, "code", None) == 429 and tentativa < _MAX_TENTATIVAS:
                espera = _espera_429(str(e))
                if on_status:
                    on_status(f"⏳ Cota da IA atingida; aguardando {espera:.0f}s...")
                time.sleep(espera)
                continue
            raise
    return ""


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
    """Devolve o rótulo do tema. Usa LLM compatível-OpenAI se LLM_BASE_URL estiver
    no .env; senão Gemini. String vazia se não der para identificar."""
    if not (texto or "").strip():
        return ""
    if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"):
        modelo = os.environ.get("LLM_MODEL", model)
        return _classificar_openai(texto, numero_processo, modelo, temperature, on_status)
    return _classificar_gemini(texto, numero_processo, model, temperature, top_p, api_key, on_status)
