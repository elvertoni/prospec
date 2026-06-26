"""Servidor web da prospecção (VPS/Easypanel).

Responsabilidades:
- Painel (basic auth): adicionar CNPJs à fila, ver status e prospects (Sheets).
- API do agente (token): reivindicar CNPJ, ingerir texto de sentença
  (classifica via Gemini e grava no Sheets).

O scraping NÃO roda aqui (Cloudflare Turnstile barra datacenter). Quem raspa é
o agente local no PC do João (ver agente/agente.py).

Variáveis de ambiente:
  AGENT_TOKEN   token compartilhado com o agente local
  PANEL_USER, PANEL_PASS   basic auth do painel
  GEMINI_API_KEY, GOOGLE_SHEETS_ID, GOOGLE_SA_JSON, SHEETS_WORKSHEET
  DB_PATH       caminho do SQLite (default data/fila.sqlite)
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Form, HTTPException, Header, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src import classificador, extrator, sheets
from src.util import so_digitos
from . import db

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config.yaml"
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app = FastAPI(title="Prospecção Tributária TRF4")
seguranca = HTTPBasic()


def cfg() -> dict:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


@app.on_event("startup")
def _startup():
    db.init()


# ---------- auth ----------
def painel_auth(cred: HTTPBasicCredentials = Depends(seguranca)) -> str:
    u = os.environ.get("PANEL_USER", "admin")
    p = os.environ.get("PANEL_PASS", "trocar")
    ok = secrets.compare_digest(cred.username, u) and secrets.compare_digest(cred.password, p)
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "credenciais inválidas",
                            {"WWW-Authenticate": "Basic"})
    return cred.username


def agente_auth(x_agent_token: str = Header(default="")) -> None:
    esperado = os.environ.get("AGENT_TOKEN", "")
    if not esperado or not secrets.compare_digest(x_agent_token, esperado):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token do agente inválido")


# ---------- painel (humano) ----------
@app.get("/", response_class=HTMLResponse)
def painel(request: Request, _: str = Depends(painel_auth)):
    try:
        registros = sheets._abrir_worksheet().get_all_records()
    except Exception as e:  # noqa: BLE001
        registros = []
        print("aviso sheets:", e)
    altos = [r for r in registros if str(r.get("oportunidade_prospeccao")).lower() == "alta"]
    return templates.TemplateResponse("index.html", {
        "request": request,
        "fila": db.listar(),
        "resumo": db.resumo(),
        "prospects": registros,
        "altos": altos,
    })


@app.post("/cnpjs")
def add_cnpjs(texto: str = Form(...), _: str = Depends(painel_auth)):
    cnpjs = [so_digitos(l) for l in texto.splitlines() if so_digitos(l)]
    n = db.adicionar(cnpjs)
    return RedirectResponse(f"/?add={n}", status_code=303)


@app.get("/health")
def health():
    return {"ok": True, "fila": db.resumo()}


# ---------- API do agente ----------
class Ingest(BaseModel):
    cnpj: str
    numero_processo: str
    nome_parte: str | None = None
    movimento: str | None = None
    texto: str


@app.get("/api/jobs/next", dependencies=[Depends(agente_auth)])
def proximo(worker: str = "agente"):
    cnpj = db.reivindicar(worker)
    if not cnpj:
        return {"cnpj": None}
    return {"cnpj": cnpj}


@app.post("/api/ingest", dependencies=[Depends(agente_auth)])
def ingest(item: Ingest):
    ws = sheets._abrir_worksheet()
    if item.numero_processo in sheets.numeros_ja_gravados(ws):
        return {"status": "duplicado"}
    recorte = extrator.recortar_relatorio_e_dispositivo(item.texto)
    g = cfg()["gemini"]
    reg = classificador.classificar(
        item.nome_parte, item.numero_processo, recorte,
        model=g["model"], temperature=g["temperature"], top_p=g["top_p"])
    sheets.gravar(reg, ws)
    return {"status": "gravado", "tema": reg.get("tema_discussao"),
            "oportunidade": reg.get("oportunidade_prospeccao")}


class JobDone(BaseModel):
    cnpj: str
    gravados: int = 0
    erro: str | None = None


@app.post("/api/jobs/done", dependencies=[Depends(agente_auth)])
def concluir(j: JobDone):
    db.concluir(j.cnpj, j.gravados, j.erro)
    return {"ok": True}
