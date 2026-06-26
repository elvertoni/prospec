"""Enriquecimento via DataJud (API pública CNJ) — grátis, sem Turnstile.

Dado um número de processo CNJ, devolve classe, assuntos (o TEMA), grau e se
há indício de sentença — tudo dos metadados oficiais, sem abrir o eproc.

Uso no pipeline: filtra os processos de um CNPJ ANTES de abrir documentos no
TRF4 (que tem Turnstile). Só os tributários com sentença vão para a coleta cara.

Cluster compartilhado do CNJ: responde 429/rejected sob carga -> retry/backoff.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .assuntos_tributarios import eh_tributario

API_KEY = ("APIKey cDZHYzlZa0JadVREZDJCendQbXY6SkJlTzNjLV9TRENyQk1RdnFKZGRQdw==")
URL = "https://api-publica.datajud.cnj.jus.br/api_publica_{tribunal}/_search"

_KW_SENTENCA = re.compile(r"senten|julgamento|proced[êe]nc|improced|m[ée]rito", re.I)


def _post(tribunal: str, body: dict, tentativas: int = 5) -> dict:
    url = URL.format(tribunal=tribunal)
    dados = json.dumps(body).encode()
    erro = None
    for i in range(tentativas):
        try:
            req = urllib.request.Request(
                url, data=dados,
                headers={"Authorization": API_KEY, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=45) as r:
                d = json.loads(r.read())
            if "hits" in d:
                return d
            erro = d.get("error")
        except Exception as e:  # noqa: BLE001
            erro = e
        time.sleep(2 * (i + 1))  # backoff linear
    raise RuntimeError(f"DataJud falhou após {tentativas} tentativas: {erro}")


def consultar_varios(numeros: list[str], tribunal: str = "trf4",
                     lote: int = 50) -> dict[str, dict]:
    """Consulta vários números (CNJ). Devolve {numero20: registro_agregado}.

    Agrega G1/G2 do mesmo processo: une assuntos e marca tem_sentenca se qualquer
    grau indicar.
    """
    so20 = [re.sub(r"\D", "", n) for n in numeros]
    agreg: dict[str, dict] = {}
    for i in range(0, len(so20), lote):
        bloco = so20[i:i + lote]
        d = _post(tribunal, {"query": {"terms": {"numeroProcesso": bloco}}, "size": lote * 4})
        for h in d["hits"]["hits"]:
            s = h["_source"]
            num = s["numeroProcesso"]
            r = agreg.setdefault(num, {
                "numero": num, "classes": set(), "assuntos": set(), "_assuntos_raw": [],
                "graus": set(), "tem_sentenca": False})
            r["classes"].add(s.get("classe", {}).get("nome", ""))
            for a in s.get("assuntos", []):
                nome = a.get("nome", "")
                if nome:
                    r["assuntos"].add(nome)
                r["_assuntos_raw"].append(a)
            r["graus"].add(s.get("grau", ""))
            movs = " ".join(m.get("nome", "") for m in s.get("movimentos", []))
            classe = s.get("classe", {}).get("nome", "")
            if _KW_SENTENCA.search(movs) or _KW_SENTENCA.search(classe):
                r["tem_sentenca"] = True
    # finaliza flags
    for r in agreg.values():
        r["tributario"] = eh_tributario(r.pop("_assuntos_raw", []))
        r["assuntos"] = sorted(a for a in r["assuntos"] if a)
        r["classes"] = sorted(c for c in r["classes"] if c)
        r["graus"] = sorted(g for g in r["graus"] if g)
    return agreg


def vale_coletar(reg: dict) -> bool:
    """Heurística: processo promissor para abrir a sentença no eproc."""
    return reg.get("tributario") and reg.get("tem_sentenca")
