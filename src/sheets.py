"""Persistência em Google Sheets via service account (gspread).

A planilha é o "banco" do MVP — advogados validam a triagem direto nela.
Cabeçalho criado na primeira execução. Cada processo classificado = 1 linha.
"""
from __future__ import annotations

import os

import gspread
from google.oauth2.service_account import Credentials

ESCOPOS = ["https://www.googleapis.com/auth/spreadsheets"]

CABECALHO = [
    "nome_cliente", "cnpj", "numero_processo", "polo", "tema_discussao",
    "tese_codigo", "tese_especifica", "resultado", "transitou_em_julgado",
    "oportunidade_prospeccao", "justificativa_oportunidade", "nova_tese_potencial",
    "trecho_evidencia", "confianca", "sigiloso", "observacao",
]


def _abrir_worksheet():
    sa_json = os.environ["GOOGLE_SA_JSON"]
    sheet_id = os.environ["GOOGLE_SHEETS_ID"]
    aba = os.environ.get("SHEETS_WORKSHEET", "prospects")

    creds = Credentials.from_service_account_file(sa_json, scopes=ESCOPOS)
    cliente = gspread.authorize(creds)
    planilha = cliente.open_by_key(sheet_id)
    try:
        ws = planilha.worksheet(aba)
    except gspread.WorksheetNotFound:
        ws = planilha.add_worksheet(title=aba, rows=1000, cols=len(CABECALHO))
    if ws.row_count == 0 or not ws.acell("A1").value:
        ws.update("A1", [CABECALHO])
    return ws


def numeros_ja_gravados(ws=None) -> set[str]:
    """Set de numero_processo já na planilha — evita duplicar."""
    ws = ws or _abrir_worksheet()
    col_idx = CABECALHO.index("numero_processo") + 1
    return {v for v in ws.col_values(col_idx)[1:] if v}


def gravar(registro: dict, ws=None) -> None:
    """Append de uma linha na planilha a partir do dict da triagem."""
    ws = ws or _abrir_worksheet()
    linha = [_fmt(registro.get(c)) for c in CABECALHO]
    ws.append_row(linha, value_input_option="USER_ENTERED")


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "sim" if v else "nao"
    return str(v)
