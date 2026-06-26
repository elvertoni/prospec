"""Orquestra: CNPJs -> coleta TRF4 (CDP) -> classifica IA -> Sheets.

Pré-requisito do scraping: Chrome aberto com --remote-debugging-port=9222 e
Turnstile resolvido pelo humano (ver README / coletor_trf4).

Uso:
    python -m src.pipeline                 # lê data/cnpjs.txt
    python -m src.pipeline 11222333000181  # CNPJ(s) na linha de comando
    python -m src.pipeline --pdf arquivo.pdf --numero 5030399-41.2011.4.04.7000 \
        --nome "Empresa X"                 # modo semi-manual (classifica 1 PDF)

Carrega .env automaticamente. Idempotente por numero_processo (não re-grava).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

from . import classificador, extrator, sheets
from .coletor_trf4 import Coletor, so_digitos

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config.yaml"
CNPJS_TXT = RAIZ / "data" / "cnpjs.txt"


def carregar_config() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def ler_cnpjs() -> list[str]:
    if not CNPJS_TXT.exists():
        return []
    linhas = CNPJS_TXT.read_text(encoding="utf-8").splitlines()
    return [so_digitos(l) for l in linhas if so_digitos(l)]


def classificar_texto(texto: str, numero: str, nome: str | None, cfg: dict) -> dict:
    """Texto da sentença -> triagem IA. Não grava (quem grava é o chamador)."""
    recorte = extrator.recortar_relatorio_e_dispositivo(texto)
    g = cfg["gemini"]
    return classificador.classificar(
        nome, numero, recorte,
        model=g["model"], temperature=g["temperature"], top_p=g["top_p"],
    )


def processar_pdf(pdf_path: str | Path, numero: str, nome: str | None, cfg: dict) -> dict:
    """PDF -> texto -> triagem IA (modo semi-manual --pdf)."""
    bruto = extrator.pdf_para_texto(pdf_path)
    return classificar_texto(bruto, numero, nome, cfg)


def coletar(cnpjs: list[str], cfg: dict, limite: int | None = None,
            on_evento=None) -> dict:
    """Coleta + classifica + grava. Reutilizável por CLI e front-end.

    on_evento(tipo, msg): callback opcional de progresso. tipo ∈
    {info, gravado, pulado, sem_sentenca, erro}. Devolve dict de estatísticas.
    """
    def emit(tipo, msg):
        if on_evento:
            on_evento(tipo, msg)
        else:
            print(msg)

    coletor = Coletor(config=cfg)
    ws = sheets._abrir_worksheet()
    ja = sheets.numeros_ja_gravados(ws)
    st = {"gravados": 0, "pulados": 0, "sem_sentenca": 0, "erros": 0, "novos": []}

    for proc in coletor.coletar_cnpjs(cnpjs, limite=limite):
        if not proc.numero_processo:
            st["erros"] += 1
            emit("erro", f"CNPJ {proc.cnpj}: {proc.erro}")
            continue
        if proc.numero_processo in ja:
            st["pulados"] += 1
            emit("pulado", f"{proc.numero_processo}: já na planilha")
            continue
        if proc.erro or not proc.texto:
            st["sem_sentenca"] += 1
            emit("sem_sentenca", f"{proc.numero_processo}: {proc.erro or 'sem texto'}")
            continue
        reg = classificar_texto(proc.texto, proc.numero_processo, proc.nome_parte, cfg)
        sheets.gravar(reg, ws)
        ja.add(proc.numero_processo)
        st["gravados"] += 1
        st["novos"].append(reg)
        emit("gravado", f"{proc.numero_processo}: {reg.get('nome_cliente')} | "
             f"{reg.get('tema_discussao')} ({reg.get('oportunidade_prospeccao')})")
    return st


def rodar_cnpjs(cnpjs: list[str], cfg: dict, limite: int | None = None) -> None:
    st = coletar(cnpjs, cfg, limite=limite)
    print(f"\nresumo: {st['gravados']} gravados, {st['pulados']} pulados, "
          f"{st['sem_sentenca']} sem sentença, {st['erros']} erros")


def main(argv: list[str] | None = None) -> int:
    load_dotenv(RAIZ / ".env")
    ap = argparse.ArgumentParser(description="Pipeline prospecção tributária TRF4")
    ap.add_argument("cnpjs", nargs="*", help="CNPJs (só dígitos). Vazio = lê data/cnpjs.txt")
    ap.add_argument("--pdf", help="Modo semi-manual: classifica este PDF e grava")
    ap.add_argument("--numero", help="numero_processo (com --pdf)")
    ap.add_argument("--nome", help="nome da parte (com --pdf)")
    ap.add_argument("--limite", type=int, default=None,
                    help="máximo de processos por CNPJ (teste/throttle)")
    args = ap.parse_args(argv)

    cfg = carregar_config()

    if args.pdf:
        if not args.numero:
            ap.error("--pdf exige --numero")
        reg = processar_pdf(args.pdf, args.numero, args.nome, cfg)
        sheets.gravar(reg)
        print(f"gravado: {reg.get('numero_processo')} -> {reg.get('tema_discussao')}")
        return 0

    cnpjs = [so_digitos(c) for c in args.cnpjs] or ler_cnpjs()
    if not cnpjs:
        print("Nenhum CNPJ. Passe na linha de comando ou preencha data/cnpjs.txt")
        return 1
    rodar_cnpjs(cnpjs, cfg, limite=args.limite)
    return 0


if __name__ == "__main__":
    sys.exit(main())
