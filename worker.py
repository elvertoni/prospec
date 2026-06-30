"""Worker desacoplado — consome a fila SQLite e processa CNPJs.

Roda como processo independente (subprocess do Streamlit ou terminal separado).
Loop principal: pega próximo pendente → conecta Chrome CDP → coleta → classifica → grava.

Requisito: Chrome aberto com --remote-debugging-port=9222 e Turnstile resolvido.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Garante que o diretório raiz está no path para imports relativos
RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv

load_dotenv(RAIZ / ".env")

from src import fila, extrator, sheets
from src.classificador import classificar_tema
from src.sessao import SessionManager, SessaoFria, SessaoNaoConectada, nome_parte_ativa, nome_patrocinador
from src.util import so_digitos

# ── Config ────────────────────────────────────────────────────────────────

THROTTLE_ENTRE_PROCESSOS = 1.5  # segundos entre processos (anti rate-limit)
THROTTLE_ENTRE_LOTES = 5.0       # segundos entre lotes (respira)
MAX_TENTATIVAS_POR_PROCESSO = 3
AQUECER_TIMEOUT = 65             # segundos aguardando Turnstile

CDP_URL = "http://127.0.0.1:9222"


def _carregar_config() -> dict:
    """Carrega config.yaml se existir (parâmetros do TRF4)."""
    import yaml

    cfg_path = RAIZ / "config.yaml"
    if cfg_path.exists():
        return yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    return {}


def _processar_lote(lote_id: int, sessao: SessionManager) -> int:
    """Processa todos os pendentes de um lote. Retorna número de concluídos."""
    concluidos = 0
    status = fila.lote_status(lote_id)
    cnpjs: list[str] = status.get("cnpjs", [])

    for cnpj in cnpjs:
        cnpj_limpo = so_digitos(cnpj)
        print(f"\n{'─'*60}")
        print(f"[CNPJ] {cnpj_limpo}")

        # 1. Listar processos do CNPJ (se ainda não listados)
        try:
            procs_raw = sessao.listar_processos(cnpj_limpo)
        except SessaoFria:
            fila.lote_pausar(lote_id, "Sessão TRF4 fria ao listar processos")
            fila.registrar_evento(None, "sessao_fria", f"CNPJ {cnpj_limpo}: sessão fria ao listar")
            return concluidos
        except Exception as e:
            print(f"  ⚠️ Erro ao listar: {e}")
            continue

        print(f"  {len(procs_raw)} processos encontrados")

        # Constrói mapa de situação por número de processo
        procs = [(n, h) for n, h, _ in procs_raw]  # compat: (numero, href)
        situacao_por_numero = {n: s for n, _, s in procs_raw if s}

        # Insere processos na fila se ainda não existirem
        for numero, href in procs:
            fila.inserir_processo(lote_id, cnpj_limpo, numero)

        # 2. Processa cada pendente deste CNPJ
        while True:
            p = fila.proximo_pendente(lote_id)
            if not p:
                break  # sem mais pendentes neste lote
            if p["cnpj"] != cnpj_limpo:
                # pertence a outro CNPJ → devolve? Não: proximo_pendente já
                # marcou como 'buscando'. Processa e segue.
                pass

            proc_id = p["id"]
            numero = p["numero_processo"]

            # Encontra o href correspondente nos resultados listados
            href = next((h for n, h in procs if n == numero), None)
            if not href:
                fila.marcar_estado(proc_id, "erro", erro="href não encontrado na lista")
                continue

            print(f"  [{numero}] processando...")

            try:
                # Health check antes de cada processo
                if not sessao.health_check():
                    if sessao.esta_frio():
                        fila.lote_pausar(lote_id, "Sessão TRF4 fria durante coleta")
                        fila.registrar_evento(proc_id, "sessao_fria", "Sessão fria detectada no health check")
                        return concluidos
                    # tenta reaquecer
                    print("  ⚠️ Health check falhou, tentando reaquecer...")
                    if not sessao.aquecer():
                        fila.lote_pausar(lote_id, "Não foi possível reaquecer a sessão")
                        fila.registrar_evento(proc_id, "sessao_fria", "Falha ao reaquecer")
                        return concluidos

                # 3. Abrir processo e achar sentença
                tab = sessao.abrir_processo(href)
                sent = sessao.achar_sentenca(tab)

                if not sent:
                    fila.marcar_estado(proc_id, "sem_sentenca")
                    fila.registrar_evento(proc_id, "info", "sem sentença de mérito")
                    continue

                doc_href, movimento = sent

                # 4. Extrair texto
                texto = sessao.texto_documento(doc_href)
                if not texto or not texto.strip():
                    fila.marcar_estado(proc_id, "sem_sentenca", erro="documento vazio")
                    fila.registrar_evento(proc_id, "info", "documento da sentença vazio")
                    continue

                # Extrair nome da parte e patrocinador
                nome = nome_parte_ativa(texto) or f"CNPJ {cnpj_limpo}"
                advogado = nome_patrocinador(texto) or ""

                fila.marcar_estado(
                    proc_id, "extraido",
                    texto_sentenca=texto,
                    nome_parte=nome,
                    movimento=movimento,
                )

                # 5. Classificar tema
                fila.marcar_estado(proc_id, "classificando")
                trecho = extrator.trecho_para_tema(texto)

                try:
                    resultado = classificar_tema(
                        trecho,
                        numero_processo=numero,
                    )
                    tema = resultado.get("tema_discussao", "")
                    desc_completa = resultado.get("descricao_completa", "")
                except Exception as e:
                    fila.marcar_estado(proc_id, "erro_ia", erro=f"IA: {e}")
                    fila.registrar_evento(proc_id, "erro", f"Classificador falhou: {e}")
                    continue

                # Se o tema vier vazio mas tem descricao_completa, usa como fallback
                if not tema and desc_completa:
                    tema = desc_completa[:120]

                fila.marcar_estado(
                    proc_id, "classificado",
                    trecho_tema=trecho,
                    tema_discussao=tema,
                    descricao_completa=desc_completa,
                )

                # 6. Gravar no Google Sheets
                situacao = situacao_por_numero.get(numero, "")
                try:
                    ws = sheets._abrir_worksheet()
                    ja = sheets.numeros_ja_gravados(ws)
                    if numero not in ja:
                        sheets.gravar(nome, numero, tema, situacao, advogado, ws)
                except Exception as e:
                    fila.marcar_estado(proc_id, "erro_ia", erro=f"Sheets: {e}")
                    fila.registrar_evento(proc_id, "erro", f"Google Sheets: {e}")
                    continue

                # 7. Concluir
                fila.marcar_concluido(
                    proc_id, nome, numero, tema,
                    situacao=situacao,
                    patrocinador=advogado,
                    descricao_completa=desc_completa,
                )
                fila.registrar_evento(proc_id, "classificacao", f"Tema: {tema}")
                concluidos += 1

                status_lote = fila.lote_status(lote_id)
                print(
                    f"  ✅ [{numero}] {nome} — {tema} "
                    f"({status_lote['concluidos']}/{status_lote['total']})"
                )

            except SessaoFria:
                fila.lote_pausar(lote_id, "Sessão TRF4 fria durante coleta")
                fila.registrar_evento(proc_id, "sessao_fria", "Sessão fria ao processar")
                return concluidos

            except Exception as e:
                fila.marcar_estado(proc_id, "erro", erro=str(e))
                fila.registrar_evento(proc_id, "erro", str(e))
                print(f"  ❌ [{numero}] erro: {e}")

            # Throttle entre processos
            time.sleep(THROTTLE_ENTRE_PROCESSOS)

        # Throttle entre CNPJs dentro do mesmo lote
        time.sleep(THROTTLE_ENTRE_LOTES)

    # Lote concluído?
    status = fila.lote_status(lote_id)
    if status["concluidos"] >= status["total"] and status["total"] > 0:
        print(f"\n🏁 Lote {lote_id} concluído!")

    return concluidos


def main():
    """Loop principal do worker."""
    print("=" * 60)
    print("  Prospec Worker 2.0 — iniciando...")
    print(f"  PID: {os.getpid()}")
    print(f"  DB:  {fila.DB_PATH}")
    print(f"  CDP: {CDP_URL}")
    print("=" * 60)

    config = _carregar_config()
    sessao = SessionManager(cdp_url=CDP_URL, config=config)

    # Conectar ao Chrome
    try:
        if not sessao.conectar():
            print("\n❌ Não foi possível conectar ao Chrome CDP.")
            print("   Abra o Chrome com --remote-debugging-port=9222 e tente novamente.")
            sys.exit(1)
        print("✅ Conectado ao Chrome via CDP")
    except Exception as e:
        print(f"\n❌ Erro ao conectar: {e}")
        sys.exit(1)

    # Aquecer sessão
    try:
        print("🔥 Aquecendo sessão TRF4...")
        if not sessao.aquecer():
            print("\n⚠️ Sessão não aqueceu — pode precisar resolver o Turnstile no Chrome.")
            print("   O worker continuará tentando. Resolva o Turnstile se aparecer.")
    except SessaoFria:
        print("\n⚠️ Sessão fria detectada. Abra o Chrome, resolva o Turnstile e reinicie o worker.")
        sessao.fechar()
        sys.exit(1)

    total_concluidos = 0

    try:
        while True:
            # Verifica se há lotes ativos
            lotes_ativos = fila.listar_lotes(ativos=True)
            if not lotes_ativos:
                print("\n⏳ Nenhum lote ativo. Aguardando...")
                time.sleep(5)
                continue

            # Pega o lote mais antigo em andamento/pendente
            lote = lotes_ativos[0]
            lote_id = lote["id"]
            lote_status = lote["status"]

            if lote_status == "pausado":
                motivo = lote.get("pausado_motivo", "desconhecido")
                print(f"\n⏸️ Lote {lote_id} pausado: {motivo}")
                print("   Reaqueça a sessão no Chrome e rode o worker novamente.")
                # tenta reaquecer periodicamente
                time.sleep(10)
                continue

            if lote_status in ("pendente", "em_andamento"):
                print(f"\n📦 Processando lote {lote_id}...")
                n = _processar_lote(lote_id, sessao)
                total_concluidos += n

    except KeyboardInterrupt:
        print("\n\n⏹️ Worker interrompido pelo usuário.")
    finally:
        sessao.fechar()
        print(f"\n📊 Total de processos concluídos nesta execução: {total_concluidos}")


if __name__ == "__main__":
    main()
