"""Classifica o texto de UM processo via Gemini, usando o prompt.xml do escritório.

O XML é o System Instruction (persona, vocabulário de teses, formato de saída).
Cada chamada manda os dados de UM processo e recebe um único objeto JSON.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from google import genai
from google.genai import types

PROMPT_XML = Path(__file__).with_name("prompt.xml")

# Campos esperados na saída (mantém em sincronia com <formato_saida> do XML).
CAMPOS_SAIDA = [
    "nome_cliente", "cnpj", "numero_processo", "polo", "tema_discussao",
    "tese_codigo", "tese_especifica", "resultado", "transitou_em_julgado",
    "oportunidade_prospeccao", "justificativa_oportunidade", "nova_tese_potencial",
    "trecho_evidencia", "confianca", "sigiloso", "observacao",
]


def _carregar_system_instruction() -> str:
    return PROMPT_XML.read_text(encoding="utf-8")


def montar_entrada(nome_parte: str | None, numero_processo: str, texto: str) -> str:
    """Formata o turno do usuário conforme <entrada> do XML."""
    nome = nome_parte or "nao_informado"
    return (
        f'METADADOS: parte = "{nome}"; numero_processo = "{numero_processo}".\n'
        f"TEXTO: {texto}"
    )


def classificar(
    nome_parte: str | None,
    numero_processo: str,
    texto: str,
    *,
    model: str = "gemini-2.5-flash",
    temperature: float = 0.1,
    top_p: float = 0.95,
    api_key: str | None = None,
) -> dict:
    """Devolve o dict JSON da triagem. Levanta se a API falhar."""
    key = api_key or os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=key)

    cfg = types.GenerateContentConfig(
        system_instruction=_carregar_system_instruction(),
        temperature=temperature,
        top_p=top_p,
        response_mime_type="application/json",
    )
    resp = client.models.generate_content(
        model=model,
        contents=montar_entrada(nome_parte, numero_processo, texto),
        config=cfg,
    )
    dados = json.loads(resp.text)
    # garante todas as chaves (preenche faltantes com None)
    for campo in CAMPOS_SAIDA:
        dados.setdefault(campo, None)
    return dados
