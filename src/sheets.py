"""Persistência em Google Sheets via service account (gspread).

Planilha de 3 colunas — o pedido do escritório:
    NOME CLIENTE | NUMERO DO PROCESSO | TEMA DA DISCUSSAO
Cada processo com sentença = 1 linha. Dedup por número do processo.
"""
from __future__ import annotations

import os

import gspread
from google.oauth2.service_account import Credentials

ESCOPOS = ["https://www.googleapis.com/auth/spreadsheets"]

CABECALHO = ["NOME CLIENTE", "NUMERO DO PROCESSO", "TEMA DA DISCUSSÃO"]


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
        ws.update(values=[CABECALHO], range_name="A1")
    return ws


def numeros_ja_gravados(ws=None) -> set[str]:
    """Set de numero_processo já na planilha — evita duplicar."""
    ws = ws or _abrir_worksheet()
    col_idx = CABECALHO.index("NUMERO DO PROCESSO") + 1
    return {v for v in ws.col_values(col_idx)[1:] if v}


def gravar(nome_cliente: str, numero_processo: str, tema: str, ws=None) -> None:
    """Append de uma linha [nome, numero, tema]."""
    ws = ws or _abrir_worksheet()
    ws.append_row(
        [nome_cliente or "", numero_processo or "", tema or ""],
        value_input_option="USER_ENTERED",
    )
