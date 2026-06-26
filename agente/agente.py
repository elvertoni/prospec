"""Agente coletor local (PC do João).

Puxa CNPJs pendentes do servidor, raspa o TRF4 via CDP (Chrome real liberado
pelo humano) e envia o texto das sentenças para o servidor classificar+gravar.

Pré-requisitos:
  1. Chrome aberto com porta de debug e Turnstile resolvido:
       & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" `
         --remote-debugging-port=9222 --user-data-dir="C:\\PROJETOS\\JOAO\\data\\chrome-debug"
  2. .env do agente com SERVER_URL e AGENT_TOKEN (ver agente/.env.example).

Uso:
  python -m agente.agente            # processa a fila até esvaziar
  python -m agente.agente --loop     # fica rodando, repoll a cada 30s
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

from src.coletor_trf4 import Coletor

RAIZ = Path(__file__).resolve().parent.parent


def carregar_cfg() -> dict:
    return yaml.safe_load((RAIZ / "config.yaml").read_text(encoding="utf-8"))


def cliente(server: str, token: str) -> httpx.Client:
    return httpx.Client(base_url=server.rstrip("/"),
                        headers={"X-Agent-Token": token}, timeout=60)


def processar_fila(cli: httpx.Client, cfg: dict, worker: str, limite: int | None) -> bool:
    """Reivindica 1 CNPJ e processa. Devolve False se a fila estava vazia."""
    r = cli.get("/api/jobs/next", params={"worker": worker})
    r.raise_for_status()
    cnpj = r.json().get("cnpj")
    if not cnpj:
        return False

    print(f"[CNPJ {cnpj}] reivindicado")
    coletor = Coletor(config=cfg)
    gravados, erro = 0, None
    try:
        for proc in coletor.coletar_cnpjs([cnpj], limite=limite):
            if not proc.texto:
                print(f"  . {proc.numero_processo}: {proc.erro or 'sem texto'}")
                continue
            resp = cli.post("/api/ingest", json={
                "cnpj": cnpj, "numero_processo": proc.numero_processo,
                "nome_parte": proc.nome_parte, "movimento": proc.movimento,
                "texto": proc.texto})
            resp.raise_for_status()
            j = resp.json()
            if j.get("status") == "gravado":
                gravados += 1
                print(f"  + {proc.numero_processo}: {j.get('tema')} ({j.get('oportunidade')})")
            else:
                print(f"  - {proc.numero_processo}: {j.get('status')}")
    except Exception as e:  # noqa: BLE001
        erro = str(e)[:300]
        print("  ! erro:", erro)
    cli.post("/api/jobs/done", json={"cnpj": cnpj, "gravados": gravados, "erro": erro})
    print(f"[CNPJ {cnpj}] concluído: {gravados} gravados")
    return True


def main(argv: list[str] | None = None) -> int:
    import os
    load_dotenv(RAIZ / "agente" / ".env")
    ap = argparse.ArgumentParser(description="Agente coletor TRF4 (local)")
    ap.add_argument("--loop", action="store_true", help="fica rodando, repoll periódico")
    ap.add_argument("--intervalo", type=int, default=30, help="segundos entre polls no --loop")
    ap.add_argument("--limite", type=int, default=None, help="máx. processos por CNPJ")
    args = ap.parse_args(argv)

    server = os.environ.get("SERVER_URL")
    token = os.environ.get("AGENT_TOKEN")
    if not server or not token:
        print("Configure SERVER_URL e AGENT_TOKEN em agente/.env")
        return 1

    cfg = carregar_cfg()
    worker = os.environ.get("WORKER_NAME", "joaopc")
    with cliente(server, token) as cli:
        while True:
            trabalhou = processar_fila(cli, cfg, worker, args.limite)
            if trabalhou:
                continue
            if not args.loop:
                print("Fila vazia. Fim.")
                return 0
            print(f"Fila vazia. Aguardando {args.intervalo}s...")
            time.sleep(args.intervalo)


if __name__ == "__main__":
    sys.exit(main())
