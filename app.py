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

import os
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
    localapp = os.environ.get("LOCALAPPDATA", "")
    candidatos = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(localapp, r"Google\Chrome\Application\chrome.exe") if localapp else "",
    ]
    exe = next((c for c in candidatos if c and Path(c).exists()), None)
    if not exe:
        return False
    PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    # perfil dedicado garante instância separada mesmo com Chrome normal aberto
    subprocess.Popen([
        exe, "--remote-debugging-port=9222",
        f"--user-data-dir={PERFIL_DIR.resolve()}", "about:blank",
    ])
    for _ in range(20):  # Chrome frio pode demorar; espera até ~10s
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

    if porta_debug_ativa():
        st.caption("✅ Chrome de depuração conectado.")
    else:
        aviso = st.empty()
        aviso.warning("🌐 Abrindo o Chrome de depuração...")
        if not abrir_chrome():
            aviso.error("❌ Não consegui abrir o Chrome. Abra manualmente com "
                        "--remote-debugging-port=9222 e tente de novo.")
            st.stop()
        aviso.info("🌐 Chrome aberto. Se aparecer o Turnstile, resolva na janela.")

    novos: list[dict] = []
    erros_lista: list[str] = []
    bloqueados = 0
    sessao_degradou = False

    with st.status("Coletando no TRF4...", expanded=True) as status:
        def log(msg: str):
            status.write(msg)

        try:
            cfg = carregar_cfg()
            g = cfg["gemini"]
            coletor = Coletor(config=cfg)

            log("🔗 Conectando à planilha...")
            ws = sheets._abrir_worksheet()
            ja = sheets.numeros_ja_gravados(ws)

            log("🔎 Buscando processos no TRF4...")
            procs = coletor.coletar_cnpjs(cnpjs, limite=limite or None, on_status=log)

            erros_lista = [p.erro for p in procs if not p.numero_processo and p.erro]
            sessao_degradou = any(
                (p.erro or "").startswith("SESSAO_DEGRADADA") for p in procs)
            pulados_sentenca = pulados_planilha = bloqueados = 0

            for proc in procs:
                if not proc.numero_processo:
                    continue
                if proc.numero_processo in ja:
                    pulados_planilha += 1
                    log(f"⏭️ {proc.numero_processo}: já estava na planilha")
                    continue
                if proc.bloqueado:
                    bloqueados += 1
                    log(f"🟡 {proc.numero_processo}: não verificado (portal bloqueou)")
                    continue
                if proc.erro or not proc.texto:
                    pulados_sentenca += 1
                    log(f"⚪ {proc.numero_processo}: {proc.erro or 'sem texto de sentença'}")
                    continue
                log(f"🧠 {proc.numero_processo}: classificando tema com IA...")
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
                log(f"✅ {proc.numero_processo}: {nome} — {tema}")

            resumo = (f"{len(novos)} gravados · {pulados_sentenca} sem sentença · "
                      f"{bloqueados} não verificados · {pulados_planilha} já existiam")
            if sessao_degradou or erros_lista:
                status.update(label=f"Interrompido — {resumo}", state="error")
            else:
                status.update(label=f"Concluído — {resumo}", state="complete")
        except Exception as e:  # noqa: BLE001
            log(f"❌ {e}")
            status.update(label="Falha na coleta", state="error")
            erros_lista = erros_lista or [str(e)]

    # ---- resultado fora do log ----
    if novos:
        st.success(f"🎉 {len(novos)} processo(s) novo(s) gravado(s) na planilha.")
        st.dataframe(pd.DataFrame(novos), use_container_width=True, hide_index=True)
    if sessao_degradou or bloqueados:
        st.warning(
            f"🟡 {bloqueados} processo(s) não verificado(s): o TRF4 parou de entregar "
            "as páginas (sessão esfriou). **Reaqueça e clique Coletar de novo** — os já "
            "gravados não repetem, só os pendentes serão tentados.\n\n"
            "Como reaquecer: no Chrome aberto, refaça a consulta (forma 'CPF/CNPJ da "
            "Parte', seção 'SJ Paraná'), resolva o Turnstile e deixe a lista carregar.")
    if not novos and not bloqueados:
        if erros_lista:
            st.error("**Não foi possível listar os processos:**\n\n" +
                     "\n".join(f"- {e}" for e in erros_lista))
            st.caption("Dica: no Chrome aberto, refaça a consulta (CPF/CNPJ da Parte, "
                       "SJ Paraná), resolva o Turnstile e deixe a lista carregar; depois Coletar.")
        else:
            st.info("Nada novo: nenhum processo com sentença de mérito (ou já estavam na "
                    "planilha). Veja o log acima para o detalhe processo a processo.")
