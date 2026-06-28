"""Painel local de prospecção tributária TRF4 (Streamlit).

Uma página: digite CNPJ(s) -> ▶ Iniciar Coleta. O painel abre o Chrome de
depuração se preciso, raspa o TRF4 via CDP, classifica o tema com IA e grava
na planilha de 3 colunas (NOME CLIENTE | NUMERO DO PROCESSO | TEMA DA DISCUSSAO).

Pré-requisito (Cloudflare Turnstile): no Chrome que abrir, faça uma consulta e
resolva o Turnstile uma vez; deixe a janela aberta.

Rodar:
    .venv\\Scripts\\streamlit.exe run app.py
"""
from __future__ import annotations

import subprocess
import time
import urllib.request
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

CDP_URL = "http://127.0.0.1:9222"
PERFIL_DIR = RAIZ / "data" / "chrome-debug"
LOGO = RAIZ / "RMSA_Logo-Horizontal-Padrao.png.webp"

st.set_page_config(page_title="Prospecção Tributária — RMSA", page_icon="⚖️", layout="wide")


def carregar_cfg() -> dict:
    return yaml.safe_load((RAIZ / "config.yaml").read_text(encoding="utf-8"))


def porta_debug_ativa() -> bool:
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def abrir_chrome() -> bool:
    """Abre o Chrome com a porta de depuração se ainda não estiver ativa."""
    if porta_debug_ativa():
        return True
    candidatos = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    exe = next((c for c in candidatos if Path(c).exists()), None)
    if not exe:
        return False
    PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        exe, "--remote-debugging-port=9222",
        f"--user-data-dir={PERFIL_DIR.resolve()}", "about:blank",
    ])
    for _ in range(10):
        time.sleep(0.5)
        if porta_debug_ativa():
            return True
    return False


# ---------- UI ----------
if LOGO.exists():
    st.image(str(LOGO), width=320)
else:
    st.markdown("### Roveda e Marcelino Sociedade de Advogados")

st.title("⚖️ Prospecção de Teses Tributárias — TRF4")
st.caption("Digite os CNPJs, clique em Coletar. A planilha enche com nome, processo e tema. "
           "Triagem por IA para validação humana (JFPR).")

with st.expander("ℹ️ Como funciona o Chrome + Turnstile", expanded=False):
    st.markdown(
        "O TRF4 usa Cloudflare Turnstile (anti-robô). O painel abre um Chrome "
        "dedicado na porta 9222. **Na primeira consulta, resolva o Turnstile** "
        "naquela janela e deixe-a aberta — o coletor reaproveita a sessão."
    )

col_input, col_config = st.columns([2, 1])
with col_input:
    texto_cnpjs = st.text_area(
        "CNPJs (um por linha, só números):", height=150,
        placeholder="81243735000148\n11222333000181",
    )
    cnpjs = [so_digitos(l) for l in texto_cnpjs.splitlines() if so_digitos(l)]
with col_config:
    limite = st.number_input(
        "Limite de processos por CNPJ (0 = todos):", min_value=0, value=0, step=1,
        help="Útil para teste rápido ou empresas com muitos processos.",
    )
    rodar = st.button("▶️ Iniciar Coleta", type="primary",
                      use_container_width=True, disabled=not cnpjs)

# ---------- Execução ----------
if rodar:
    if not cnpjs:
        st.warning("Insira ao menos um CNPJ.")
        st.stop()

    chrome = st.empty()
    if porta_debug_ativa():
        chrome.success("✅ Chrome de depuração conectado.")
    else:
        chrome.warning("🌐 Abrindo o Chrome de depuração...")
        if not abrir_chrome():
            chrome.error("❌ Não consegui abrir o Chrome. Abra manualmente com "
                         "--remote-debugging-port=9222 e tente de novo.")
            st.stop()
        chrome.success("✅ Chrome aberto. Faça uma consulta e resolva o Turnstile, se aparecer.")
    time.sleep(1)
    chrome.empty()

    status_box = st.empty()
    prog = st.progress(0.0)

    def atualizar(msg: str):
        status_box.markdown(f"**Status:** {msg}")

    try:
        cfg = carregar_cfg()
        g = cfg["gemini"]
        coletor = Coletor(config=cfg)

        atualizar("Conectando à planilha...")
        ws = sheets._abrir_worksheet()
        ja = sheets.numeros_ja_gravados(ws)

        atualizar("Buscando processos no TRF4...")
        procs = coletor.coletar_cnpjs(cnpjs, limite=limite or None, on_status=atualizar)

        total = len(procs) or 1
        novos: list[dict] = []
        for i, proc in enumerate(procs, 1):
            prog.progress(i / total)
            if not proc.numero_processo or proc.numero_processo in ja:
                continue
            if proc.erro or not proc.texto:
                continue
            atualizar(f"Classificando tema (IA): {proc.numero_processo}...")
            tema = classificar_tema(
                extrator.trecho_para_tema(proc.texto),
                numero_processo=proc.numero_processo,
                model=g["model"], temperature=g["temperature"], top_p=g["top_p"],
            )
            nome = proc.nome_parte or f"CNPJ {proc.cnpj}"
            sheets.gravar(nome, proc.numero_processo, tema, ws)
            ja.add(proc.numero_processo)
            novos.append({
                "NOME CLIENTE": nome,
                "NUMERO DO PROCESSO": proc.numero_processo,
                "TEMA DA DISCUSSÃO": tema,
            })

        prog.empty()
        status_box.empty()
        if novos:
            st.success(f"🎉 {len(novos)} processo(s) novo(s) gravado(s) na planilha.")
            st.dataframe(pd.DataFrame(novos), use_container_width=True, hide_index=True)
        else:
            st.info("Nada novo: sem sentença de mérito ou já estavam na planilha.")
    except Exception as e:  # noqa: BLE001
        prog.empty()
        status_box.empty()
        st.error(f"Falha na coleta: {e}")
        st.caption("Confira se o Chrome 9222 está aberto e o Turnstile resolvido.")
