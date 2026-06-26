"""Painel operacional — Prospecção Tributária TRF4 (Roveda e Marcelino).

Uso (João não precisa de terminal): dê duplo clique em `iniciar.bat`.
Ou: streamlit run dashboard.py

Aba OPERAR: cola CNPJs -> abre o Chrome p/ resolver o Turnstile -> coleta.
Aba PROSPECTS: vê/filtra os resultados gravados no Google Sheets.
"""
from __future__ import annotations

import re
import subprocess
import urllib.request
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from src import pipeline, sheets
from src.coletor_trf4 import so_digitos

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")

CDP_URL = "http://127.0.0.1:9222"
PERFIL = RAIZ / "data" / "chrome-debug"
URL_LISTA = (
    "https://consulta.trf4.jus.br/trf4/controlador.php?"
    "acao=consulta_processual_valida_pesquisa&selForma=CP"
    "&txtValor={cnpj}&selOrigem=PR&chkMostrarBaixados=S&txtOrigemPesquisa=1"
)
CHROME_CANDIDATOS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def chrome_exe() -> str | None:
    return next((c for c in CHROME_CANDIDATOS if Path(c).exists()), None)


def porta_viva() -> bool:
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=3)
        return True
    except Exception:
        return False


def abrir_chrome(cnpj: str) -> None:
    exe = chrome_exe()
    if not exe:
        st.error("Chrome não encontrado nos caminhos padrão. Instale o Google Chrome.")
        return
    subprocess.Popen([
        exe, "--remote-debugging-port=9222",
        f"--user-data-dir={PERFIL}",
        URL_LISTA.format(cnpj=cnpj),
    ])


st.set_page_config(page_title="Prospecção Tributária — RMSA", layout="wide")
LOGO = RAIZ / "RMSA_Logo-Horizontal-Padrao.png.webp"
if LOGO.exists():
    st.image(str(LOGO), width=300)
st.title("Prospecção de Teses Tributárias — TRF4")
st.caption("Triagem por IA — validação humana obrigatória. Não é parecer jurídico.")

aba_operar, aba_prospects = st.tabs(["🔎 Operar", "📋 Prospects"])

# ----------------------------------------------------------------- OPERAR
with aba_operar:
    st.subheader("1. CNPJs para prospectar")
    txt = st.text_area("Um CNPJ por linha (com ou sem pontuação):", height=120,
                       placeholder="81.243.735/0001-48\n11.222.333/0001-81")
    cnpjs = [so_digitos(l) for l in txt.splitlines() if so_digitos(l)]
    if cnpjs:
        st.caption(f"{len(cnpjs)} CNPJ(s) válido(s).")

    st.subheader("2. Abrir o portal e resolver o Turnstile")
    st.write("Clique abaixo: abre o Chrome no TRF4. **Resolva o desafio Cloudflare** "
             "e espere a lista de processos carregar. Depois volte aqui.")
    col1, col2 = st.columns([1, 2])
    with col1:
        if st.button("🌐 Abrir Chrome", disabled=not cnpjs, use_container_width=True):
            abrir_chrome(cnpjs[0])
            st.info("Chrome aberto. Resolva o Turnstile na janela.")
    with col2:
        if porta_viva():
            st.success("Navegador conectado (porta 9222 ativa). Pode coletar.")
        else:
            st.warning("Navegador ainda não detectado. Abra o Chrome acima e resolva o Turnstile.")

    st.subheader("3. Coletar e classificar")
    limite = st.number_input("Máx. processos por CNPJ (0 = todos):", min_value=0, value=5, step=1)
    if st.button("▶️ Coletar agora", type="primary", disabled=not cnpjs):
        if not porta_viva():
            st.error("Navegador não conectado. Faça o passo 2 primeiro.")
        else:
            cfg = pipeline.carregar_config()
            log = st.container(height=300)
            barra = st.progress(0.0, text="Coletando...")
            contagem = {"n": 0}

            def on_evento(tipo, msg):
                icones = {"gravado": "✅", "pulado": "⏭️", "sem_sentenca": "·",
                          "erro": "⚠️", "info": "ℹ️"}
                log.write(f"{icones.get(tipo, '•')} {msg}")
                contagem["n"] += 1
                barra.progress(min(contagem["n"] / max(len(cnpjs) * (limite or 20), 1), 1.0),
                               text="Coletando...")

            with st.spinner("Rodando coleta (pode demorar)..."):
                stats = pipeline.coletar(cnpjs, cfg, limite=(limite or None), on_evento=on_evento)
            barra.progress(1.0, text="Concluído")
            st.success(f"Concluído: {stats['gravados']} novos prospects, "
                       f"{stats['pulados']} já existiam, {stats['sem_sentenca']} sem sentença, "
                       f"{stats['erros']} erros. Veja a aba Prospects.")

# -------------------------------------------------------------- PROSPECTS
with aba_prospects:
    @st.cache_data(ttl=30)
    def carregar() -> pd.DataFrame:
        return pd.DataFrame(sheets._abrir_worksheet().get_all_records())

    if st.button("🔄 Atualizar"):
        st.cache_data.clear()
    df = carregar()
    if df.empty:
        st.info("Sem prospects ainda. Use a aba Operar.")
    else:
        c1, c2, c3 = st.columns(3)
        with c1:
            op = st.multiselect("Oportunidade", sorted(df["oportunidade_prospeccao"].dropna().unique()),
                                default=["alta"])
        with c2:
            polo = st.multiselect("Polo", sorted(df["polo"].dropna().unique()))
        with c3:
            tema = st.multiselect("Tema", sorted(df["tema_discussao"].dropna().unique()))
        f = df.copy()
        if op:
            f = f[f["oportunidade_prospeccao"].isin(op)]
        if polo:
            f = f[f["polo"].isin(polo)]
        if tema:
            f = f[f["tema_discussao"].isin(tema)]

        m1, m2, m3 = st.columns(3)
        m1.metric("Prospects (filtro)", len(f))
        m2.metric("Alta oportunidade", int((df["oportunidade_prospeccao"] == "alta").sum()))
        m3.metric("Polo ativo", int((df["polo"] == "ativo").sum()))

        cols = [c for c in ["nome_cliente", "numero_processo", "tema_discussao", "polo",
                            "oportunidade_prospeccao", "nova_tese_potencial", "confianca",
                            "justificativa_oportunidade"] if c in f.columns]
        st.dataframe(f[cols], use_container_width=True, hide_index=True)
        with st.expander("Ver todas as colunas"):
            st.dataframe(f, use_container_width=True, hide_index=True)
