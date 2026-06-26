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
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Header, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from src import classificador, extrator, sheets
from src.enriquecimento import enriquecer_registro
from src.util import so_digitos
from . import db

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config.yaml"
load_dotenv(RAIZ / ".env")
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
def painel(
    request: Request,
    oportunidade: str = "",
    status_fila: str = "",
    busca: str = "",
    _: str = Depends(painel_auth),
):
    try:
        registros = sheets._abrir_worksheet().get_all_records()
    except Exception as e:  # noqa: BLE001
        registros = []
        print("aviso sheets:", e)

    fila = db.listar()
    oportunidade = oportunidade.strip().lower()
    status_fila = status_fila.strip().lower()
    busca = busca.strip()

    prospects = _filtrar_prospects(registros, oportunidade, busca)
    fila_filtrada = [j for j in fila if not status_fila or j.get("status") == status_fila]
    altos = [r for r in registros if _lower(r.get("oportunidade_prospeccao")) == "alta"]

    return templates.TemplateResponse(request, "index.html", {
        "request": request,
        "fila": fila_filtrada,
        "resumo": db.resumo(),
        "sem_prospect": sum(
            1 for j in fila
            if j.get("status") == "concluido" and int(j.get("gravados") or 0) == 0
        ),
        "prospects": prospects,
        "total_prospects": len(registros),
        "altos": altos,
        "filtros": {
            "oportunidade": oportunidade,
            "status_fila": status_fila,
            "busca": busca,
        },
        "opcoes_oportunidade": _opcoes(registros, "oportunidade_prospeccao"),
        "opcoes_status": _opcoes(fila, "status"),
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
    enriquecer_registro(reg, cnpj=item.cnpj, nome_parte=item.nome_parte)
    sheets.gravar(reg, ws)
    return {"status": "gravado", "tema": reg.get("tema_discussao"),
            "oportunidade": reg.get("oportunidade_prospeccao")}


def _lower(v) -> str:
    return str(v or "").strip().lower()


def _opcoes(rows: list[dict], campo: str) -> list[str]:
    return sorted({_lower(r.get(campo)) for r in rows if _lower(r.get(campo))})


def _filtrar_prospects(registros: list[dict], oportunidade: str, busca: str) -> list[dict]:
    termos = _lower(busca)
    campos_busca = (
        "nome_cliente", "cnpj", "numero_processo", "tema_discussao",
        "tese_especifica", "justificativa_oportunidade",
    )
    out = []
    for r in registros:
        if oportunidade and _lower(r.get("oportunidade_prospeccao")) != oportunidade:
            continue
        if termos:
            haystack = " ".join(_lower(r.get(c)) for c in campos_busca)
            if termos not in haystack:
                continue
        out.append(r)
    return out


class JobDone(BaseModel):
    cnpj: str
    gravados: int = 0
    erro: str | None = None


@app.post("/api/jobs/done", dependencies=[Depends(agente_auth)])
def concluir(j: JobDone):
    db.concluir(j.cnpj, j.gravados, j.erro)
    return {"ok": True}
