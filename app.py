"""Painel local de prospecção tributária TRF4 (Streamlit).

Uma tela: cole CNPJ(s) -> ▶ Coletar -> planilha de 3 colunas
(NOME CLIENTE | NUMERO DO PROCESSO | TEMA DA DISCUSSAO).

Pré-requisito (Cloudflare Turnstile): Chrome aberto com a porta de debug e o
Turnstile resolvido à mão UMA vez:
    & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" `
      --remote-debugging-port=9222 --user-data-dir="C:\\PROJETOS\\JOAO\\data\\chrome-debug"

Rodar:
    .venv\\Scripts\\streamlit.exe run app.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
import yaml
from dotenv import load_dotenv

from src import extrator, sheets
from src.classificador import classificar_tema
from src.coletor_trf4 import Coletor
from src.util import so_digitos

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")

st.set_page_config(page_title="Prospecção Tributária TRF4", page_icon="⚖️", layout="wide")


def carregar_cfg() -> dict:
    return yaml.safe_load((RAIZ / "config.yaml").read_text(encoding="utf-8"))


def coletar(cnpjs: list[str], limite: int | None, prog) -> list[dict]:
    """Coleta + classifica tema + grava. Devolve linhas novas para a tabela."""
    cfg = carregar_cfg()
    g = cfg["gemini"]
    coletor = Coletor(config=cfg)

    ws = sheets._abrir_worksheet()
    ja = sheets.numeros_ja_gravados(ws)
    novas: list[dict] = []

    procs = coletor.coletar_cnpjs(cnpjs, limite=limite)
    total = len(procs) or 1
    for i, proc in enumerate(procs, 1):
        prog.progress(i / total, text=f"{proc.cnpj} · {proc.numero_processo or '...'}")
        if not proc.numero_processo or proc.numero_processo in ja:
            continue
        if proc.erro or not proc.texto:
            continue
        tema = classificar_tema(
            extrator.trecho_para_tema(proc.texto),
            numero_processo=proc.numero_processo,
            model=g["model"], temperature=g["temperature"], top_p=g["top_p"],
        )
        nome = proc.nome_parte or f"CNPJ {proc.cnpj}"
        sheets.gravar(nome, proc.numero_processo, tema, ws)
        ja.add(proc.numero_processo)
        novas.append({
            "NOME CLIENTE": nome,
            "NUMERO DO PROCESSO": proc.numero_processo,
            "TEMA DA DISCUSSAO": tema,
        })
    return novas


# ---------- UI ----------
st.title("⚖️ Prospecção Tributária — TRF4")
st.caption("Cole os CNPJs, clique em Coletar. A planilha enche com nome, processo e tema. "
           "Triagem por IA para validação humana.")

with st.expander("ℹ️ Antes de coletar (Chrome + Turnstile)", expanded=False):
    st.markdown(
        "1. Feche o Chrome e abra com a porta de debug:\n"
        "```\nchrome.exe --remote-debugging-port=9222 "
        '--user-data-dir="C:\\PROJETOS\\JOAO\\data\\chrome-debug"\n```\n'
        "2. Faça a consulta de um CNPJ e **resolva o Turnstile** uma vez. Deixe aberto.\n"
        "3. Volte aqui e clique **Coletar**."
    )

col1, col2 = st.columns([3, 1])
with col1:
    texto = st.text_area("CNPJs (um por linha, só números)", height=160,
                         placeholder="81243735000148")
with col2:
    limite = st.number_input("Limite por CNPJ (0 = todos)", min_value=0, value=0, step=1)
    rodar = st.button("▶ Coletar", type="primary", use_container_width=True)

if rodar:
    cnpjs = [so_digitos(l) for l in texto.splitlines() if so_digitos(l)]
    if not cnpjs:
        st.warning("Informe ao menos um CNPJ.")
    else:
        prog = st.progress(0.0, text="Conectando ao Chrome...")
        try:
            novas = coletar(cnpjs, limite or None, prog)
            prog.empty()
            if novas:
                st.success(f"{len(novas)} processo(s) novo(s) gravado(s) na planilha.")
                st.dataframe(pd.DataFrame(novas), use_container_width=True, hide_index=True)
            else:
                st.info("Nada novo: sem sentença de mérito ou já estavam na planilha.")
        except Exception as e:  # noqa: BLE001
            prog.empty()
            st.error(f"Falha na coleta: {e}")
            st.caption("Verifique se o Chrome está aberto na porta 9222 com o Turnstile resolvido.")
