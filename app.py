"""Painel de controle de prospecção tributária TRF4 (Streamlit v2).

Duas abas:
1. 📋 Enfileirar CNPJs — adiciona lotes à fila SQLite
2. 📊 Dashboard — monitora worker, sessão Chrome e processos

O worker desacoplado (worker.py) consome a fila, raspa o TRF4 via CDP,
classifica temas com IA e grava na planilha.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

# src.fila — persistência SQLite com máquina de estados (criado pelo Agente A)
from src.fila import criar_lote, listar_lotes

RAIZ = Path(__file__).resolve().parent
load_dotenv(RAIZ / ".env")

CDP_URL = "http://127.0.0.1:9222"
DB_PATH = RAIZ / "data" / "fila.sqlite"
LOGO = RAIZ / "RMSA_Logo-Horizontal-Padrao.png.webp"

st.set_page_config(
    page_title="Prospecção Tributária — RMSA",
    page_icon="⚖️",
    layout="wide",
)


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _conectar_db() -> sqlite3.Connection | None:
    """Conecta ao SQLite da fila. Retorna None se o arquivo ainda não existir."""
    if not DB_PATH.exists():
        return None
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def porta_debug_ativa() -> bool:
    """Verifica se o Chrome de depuração está respondendo no CDP."""
    try:
        with urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def status_chrome() -> tuple[str, str, str]:
    """Retorna (emoji, rótulo, cor_streamlit).

    🟢 verde  = conectado com sessão TRF4 ativa
    🔴 vermelho = offline (porta 9222 não responde)
    🟠 laranja = conectado mas potencialmente frio
    """
    if not porta_debug_ativa():
        return "🔴", "Offline — Chrome não responde na porta 9222", "red"

    try:
        with urllib.request.urlopen(f"{CDP_URL}/json", timeout=2) as r:
            pages = json.loads(r.read().decode())
    except Exception:
        return "🟠", "Conectado (não foi possível inspecionar abas)", "orange"

    if not pages:
        return "🟠", "Conectado (sem abas abertas)", "orange"

    tem_trf4 = any(
        "trf4" in p.get("title", "").lower()
        or "tribunal" in p.get("title", "").lower()
        or "processo" in p.get("url", "").lower()
        for p in pages
    )
    if tem_trf4:
        return "🟢", "Conectado — Sessão TRF4 ativa", "green"
    else:
        return "🟠", "Conectado (sessão pode estar fria — reaqueça o Chrome)", "orange"


def _contar_por_estado() -> dict[str, int]:
    """Lê contagens de processos agrupadas por estado (dashboard)."""
    conn = _conectar_db()
    if conn is None:
        return {"pendente": 0, "concluido": 0, "erro": 0, "bloqueado": 0}
    try:
        cur = conn.execute(
            "SELECT estado, COUNT(*) AS cnt FROM processos GROUP BY estado"
        )
        contagens = {row["estado"]: row["cnt"] for row in cur.fetchall()}
    except Exception:
        contagens = {}
    finally:
        conn.close()

    return {
        "pendente": (
            contagens.get("pendente", 0)
            + contagens.get("buscando", 0)
        ),
        "concluido": (
            contagens.get("concluido", 0)
            + contagens.get("classificado", 0)
            + contagens.get("classificando", 0)
        ),
        "erro": (
            contagens.get("erro", 0)
            + contagens.get("erro_ia", 0)
        ),
        "bloqueado": contagens.get("bloqueado", 0),
    }


def _processos_recentes(limit: int = 50) -> list[dict]:
    """Retorna os processos mais recentes para a tabela do dashboard."""
    conn = _conectar_db()
    if conn is None:
        return []
    try:
        cur = conn.execute(
            """SELECT id, lote_id, cnpj, numero_processo, nome_parte,
                      estado, tema_discussao, erro, tentativas, atualizado_em
               FROM processos
               ORDER BY atualizado_em DESC
               LIMIT ?""",
            (limit,),
        )
        rows = [dict(row) for row in cur.fetchall()]
    except Exception:
        rows = []
    finally:
        conn.close()
    return rows


def _worker_ativo() -> bool:
    """Verifica se o subprocesso do worker ainda está rodando."""
    proc = st.session_state.get("worker_process")
    if proc is None:
        return False
    return proc.poll() is None


# ═══════════════════════════════════════════════════════════════════════════
# Aba 1 — Enfileirar CNPJs
# ═══════════════════════════════════════════════════════════════════════════

def aba_enfileirar():
    """Formulário para criar um lote de CNPJs na fila."""
    from src.util import so_digitos

    texto_cnpjs = st.text_area(
        "CNPJs (um por linha, só números):",
        height=150,
        placeholder="81243735000148\n11222333000181",
        key="enfileirar_cnpjs",
    )

    col1, col2 = st.columns([1, 1])
    with col1:
        limite = st.number_input(
            "Limite de processos por CNPJ (0 = todos):",
            min_value=0,
            value=0,
            step=1,
            help="Útil para teste rápido ou empresas com muitos processos.",
            key="enfileirar_limite",
        )
    with col2:
        st.write("")  # espaçamento
        st.write("")
        enfileirar_btn = st.button(
            "📥 Enfileirar",
            type="primary",
            use_container_width=True,
            disabled=not texto_cnpjs.strip(),
            key="enfileirar_btn",
        )

    if enfileirar_btn:
        cnpjs = [so_digitos(l) for l in texto_cnpjs.splitlines() if so_digitos(l)]
        if not cnpjs:
            st.warning("Insira ao menos um CNPJ válido.")
            return

        try:
            lote_id = criar_lote(cnpjs, limite=limite)
            st.success(
                f"✅ **{len(cnpjs)}** CNPJ(s) enfileirado(s) no lote **#{lote_id}** "
                f"(limite: {limite or 'todos'} processos por CNPJ)."
            )
            st.info("💡 Vá para a aba **📊 Dashboard** e inicie o worker para processar.")
        except Exception as e:
            st.error(f"❌ Erro ao criar lote: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# Aba 2 — Dashboard
# ═══════════════════════════════════════════════════════════════════════════

def aba_dashboard():
    """Painel de monitoramento: cards, status da sessão, worker e tabela."""

    # ── Cards de contagem ──
    contagens = _contar_por_estado()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📋 Pendentes", contagens["pendente"])
    c2.metric("✅ Concluídos", contagens["concluido"])
    c3.metric("❌ Erros", contagens["erro"])
    c4.metric("🔒 Bloqueados", contagens["bloqueado"])

    st.divider()

    # ── Status da sessão Chrome ──
    emoji, rotulo, cor = status_chrome()
    st.markdown(f"**Sessão Chrome:** :{cor}[{emoji} {rotulo}]")

    st.divider()

    # ── Controles do Worker ──
    col_w1, col_w2, col_w3 = st.columns([1, 1, 1])
    with col_w1:
        if st.button(
            "▶️ Iniciar Worker",
            type="primary",
            use_container_width=True,
            disabled=_worker_ativo(),
            key="btn_iniciar_worker",
        ):
            _iniciar_worker()
    with col_w2:
        if st.button(
            "⏹️ Parar Worker",
            use_container_width=True,
            disabled=not _worker_ativo(),
            key="btn_parar_worker",
        ):
            _parar_worker()
    with col_w3:
        if st.button("🔄 Atualizar", use_container_width=True, key="btn_atualizar"):
            st.rerun()

    # Status do worker
    if _worker_ativo():
        pid = st.session_state.get("worker_pid", "?")
        st.info(f"⚙️ Worker em execução (PID {pid}).")
    else:
        proc = st.session_state.get("worker_process")
        if proc is not None:
            ret = proc.poll()
            st.warning(f"⚠️ Worker encerrou com código {ret}.")
        else:
            st.caption("💤 Worker parado. Clique **Iniciar Worker** para processar a fila.")

    st.divider()

    # ── Auto-refresh toggle ──
    st.session_state.auto_refresh = st.checkbox(
        "🔄 Auto-refresh (5s)",
        value=st.session_state.get("auto_refresh", True),
        key="auto_refresh_toggle",
        help="Atualiza o dashboard automaticamente a cada 5 segundos.",
    )

    st.divider()

    # ── Tabela de processos recentes ──
    st.subheader("📋 Processos Recentes (últimos 50)")
    recentes = _processos_recentes(50)
    if recentes:
        df = pd.DataFrame(recentes)
        colunas = [
            "id", "lote_id", "cnpj", "numero_processo", "nome_parte",
            "estado", "tema_discussao", "situacao", "patrocinador",
            "erro", "tentativas", "atualizado_em",
        ]
        df_exibir = df[[c for c in colunas if c in df.columns]]
        st.dataframe(
            df_exibir,
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": "ID",
                "lote_id": "Lote",
                "cnpj": "CNPJ",
                "numero_processo": "Processo",
                "nome_parte": "Parte",
                "estado": "Estado",
                "tema_discussao": "Tema",
                "situacao": "Situação",
                "patrocinador": "Patrocinador",
                "erro": "Erro",
                "tentativas": "Tent.",
                "atualizado_em": "Atualizado",
            },
        )
    else:
        st.info("📭 Nenhum processo na fila ainda. Enfileire CNPJs na aba ao lado.")


def _iniciar_worker():
    """Inicia worker.py como subprocesso e armazena no session_state."""
    proc = st.session_state.get("worker_process")
    if proc is not None and proc.poll() is None:
        st.warning("⚠️ Worker já está em execução.")
        return

    worker_py = RAIZ / "worker.py"
    if not worker_py.exists():
        st.error(f"❌ Arquivo não encontrado: {worker_py}")
        return

    try:
        proc = subprocess.Popen(
            ["python", "worker.py"],
            cwd=str(RAIZ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        st.session_state.worker_process = proc
        st.session_state.worker_pid = proc.pid
        st.success(f"✅ Worker iniciado (PID {proc.pid}).")
    except Exception as e:
        st.error(f"❌ Erro ao iniciar worker: {e}")


def _parar_worker():
    """Para o worker (terminate → kill se necessário)."""
    proc = st.session_state.get("worker_process")
    if proc is None:
        st.warning("⚠️ Nenhum worker em execução.")
        return

    poll = proc.poll()
    if poll is not None:
        st.info(f"ℹ️ Worker já encerrou (código {poll}).")
        st.session_state.worker_process = None
        st.session_state.worker_pid = None
        return

    try:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        st.success("✅ Worker parado.")
    except Exception as e:
        st.error(f"❌ Erro ao parar worker: {e}")
    finally:
        st.session_state.worker_process = None
        st.session_state.worker_pid = None


# ═══════════════════════════════════════════════════════════════════════════
# UI Principal
# ═══════════════════════════════════════════════════════════════════════════

def main():
    # ── Logo e cabeçalho ──
    if LOGO.exists():
        st.image(str(LOGO), width=320)
    else:
        st.markdown("### Roveda e Marcelino Sociedade de Advogados")

    st.title("⚖️ Prospecção de Teses Tributárias — TRF4")
    st.caption(
        "Painel de controle v2: enfileire CNPJs e acompanhe o processamento. "
        "O worker desacoplado raspa o TRF4, classifica temas com IA e grava na planilha."
    )

    # ── Abas ──
    tab1, tab2 = st.tabs(["📋 Enfileirar CNPJs", "📊 Dashboard"])

    with tab1:
        aba_enfileirar()

    with tab2:
        aba_dashboard()

    # ── Auto-refresh ──
    if st.session_state.get("auto_refresh", True):
        time.sleep(5)
        st.rerun()


if __name__ == "__main__":
    main()
