"""Fila de CNPJs em SQLite (estado do trabalho). Prospects ficam no Sheets.

Estados: pendente -> processando -> concluido | erro.
O agente local reivindica um CNPJ pendente, processa e reporta de volta.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(os.environ.get("DB_PATH", "data/fila.sqlite"))


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def init() -> None:
    with _conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS jobs (
                cnpj TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pendente',
                worker TEXT,
                gravados INTEGER DEFAULT 0,
                erro TEXT,
                criado_em REAL NOT NULL,
                atualizado_em REAL NOT NULL
            )"""
        )


def adicionar(cnpjs: list[str]) -> int:
    agora = time.time()
    novos = 0
    with _conn() as c:
        for cnpj in cnpjs:
            cur = c.execute(
                "INSERT OR IGNORE INTO jobs(cnpj,status,criado_em,atualizado_em) "
                "VALUES (?,?,?,?)", (cnpj, "pendente", agora, agora))
            novos += cur.rowcount
    return novos


def reivindicar(worker: str) -> str | None:
    """Marca um CNPJ pendente como 'processando' e devolve. None se fila vazia."""
    agora = time.time()
    with _conn() as c:
        row = c.execute(
            "SELECT cnpj FROM jobs WHERE status='pendente' ORDER BY criado_em LIMIT 1"
        ).fetchone()
        if not row:
            return None
        cnpj = row["cnpj"]
        c.execute("UPDATE jobs SET status='processando', worker=?, atualizado_em=? "
                  "WHERE cnpj=?", (worker, agora, cnpj))
        return cnpj


def concluir(cnpj: str, gravados: int, erro: str | None = None) -> None:
    with _conn() as c:
        c.execute("UPDATE jobs SET status=?, gravados=?, erro=?, atualizado_em=? WHERE cnpj=?",
                  ("erro" if erro else "concluido", gravados, erro, time.time(), cnpj))


def listar() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM jobs ORDER BY atualizado_em DESC").fetchall()]


def resumo() -> dict:
    with _conn() as c:
        rows = c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
