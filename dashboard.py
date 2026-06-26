"""Dashboard Streamlit — lê a planilha de prospects e exibe/filtra.

    streamlit run dashboard.py

Lê a mesma Google Sheet que o pipeline grava. Foco: priorizar oportunidades
'alta' no polo ativo. Validação humana acontece editando a própria planilha.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src import sheets

load_dotenv(Path(__file__).resolve().parent / ".env")

st.set_page_config(page_title="Prospecção Tributária — RMSA", layout="wide")

LOGO = Path(__file__).resolve().parent / "RMSA_Logo-Horizontal-Padrao.png.webp"
if LOGO.exists():
    st.image(str(LOGO), width=320)
st.title("Prospecção de Teses Tributárias — TRF4")
st.caption("Triagem por IA — validação humana obrigatória. Não é parecer jurídico.")


@st.cache_data(ttl=60)
def carregar() -> pd.DataFrame:
    ws = sheets._abrir_worksheet()
    registros = ws.get_all_records()
    return pd.DataFrame(registros)


df = carregar()
if df.empty:
    st.info("Sem prospects ainda. Rode `python -m src.pipeline` para popular.")
    st.stop()

# --- filtros ------------------------------------------------------------
c1, c2, c3 = st.columns(3)
with c1:
    op = st.multiselect("Oportunidade", sorted(df["oportunidade_prospeccao"].dropna().unique()),
                        default=["alta"])
with c2:
    polo = st.multiselect("Polo", sorted(df["polo"].dropna().unique()))
with c3:
    tema = st.multiselect("Tema", sorted(df["tema_discussao"].dropna().unique()))

filtrado = df.copy()
if op:
    filtrado = filtrado[filtrado["oportunidade_prospeccao"].isin(op)]
if polo:
    filtrado = filtrado[filtrado["polo"].isin(polo)]
if tema:
    filtrado = filtrado[filtrado["tema_discussao"].isin(tema)]

# --- métricas -----------------------------------------------------------
m1, m2, m3 = st.columns(3)
m1.metric("Prospects (filtro)", len(filtrado))
m2.metric("Alta oportunidade", int((df["oportunidade_prospeccao"] == "alta").sum()))
m3.metric("Polo ativo", int((df["polo"] == "ativo").sum()))

# --- tabela -------------------------------------------------------------
colunas = ["nome_cliente", "numero_processo", "tema_discussao", "polo",
           "oportunidade_prospeccao", "nova_tese_potencial", "confianca",
           "justificativa_oportunidade"]
colunas = [c for c in colunas if c in filtrado.columns]
st.dataframe(filtrado[colunas], use_container_width=True, hide_index=True)

with st.expander("Ver registro completo (todas as colunas)"):
    st.dataframe(filtrado, use_container_width=True, hide_index=True)
