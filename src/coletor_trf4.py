"""Coletor TRF4 (eProc) — via CDP no Chrome real liberado pelo humano.

O portal consulta.trf4.jus.br está atrás de Cloudflare Turnstile. Automação pura
(Playwright lançando o browser) é flagada como bot e o Turnstile FALHA mesmo com
humano resolvendo. Solução que funciona (confirmada 2026-06):

  1. Humano abre o Chrome NORMAL com porta de debug e resolve o Turnstile 1x:
       & "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" `
         --remote-debugging-port=9222 `
         --user-data-dir="C:\\PROJETOS\\JOAO\\data\\chrome-debug"
  2. Este coletor ANEXA na sessão já liberada via CDP e dirige a navegação:
       lista processos do CNPJ -> abre cada um -> expande eventos ->
       acha a SENTENÇA -> lê o TEXTO do documento (HTML, sem PDF).

A sessão (PHPSESSID) liberada pelo humano basta; não há cf_clearance persistente.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import datajud
from .util import CNJ, so_digitos

RAIZ = Path(__file__).resolve().parent.parent

CDP_URL = "http://127.0.0.1:9222"

URL_LISTA = (
    "https://consulta.trf4.jus.br/trf4/controlador.php?"
    "acao=consulta_processual_valida_pesquisa&selForma=CP"
    "&txtValor={cnpj}&selOrigem=PR&chkMostrarBaixados=S&txtOrigemPesquisa=1"
)

# rótulos do polo ativo no cabeçalho da sentença (eProc)
_POLO_ATIVO = r"(?:AUTOR(?:A)?|IMPETRANTE|REQUERENTE|EXEQUENTE|EMBARGANTE|AGRAVANTE|APELANTE)"
_RE_NOME = re.compile(_POLO_ATIVO + r"\s*:\s*(.+?)\s*(?:ADVOGAD|IMPETRAD|R[ÉE]U|REQUERID|EXECUTAD|MPF|MINIST[ÉE]RIO|\n)", re.I)


def nome_parte_ativa(texto: str) -> str | None:
    """Extrai o nome do polo ativo do cabeçalho da sentença, se houver."""
    m = _RE_NOME.search(texto or "")
    if m:
        nome = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
        return nome[:120] or None
    return None


@dataclass
class ProcessoColetado:
    cnpj: str
    numero_processo: str
    nome_parte: str | None = None
    movimento: str | None = None   # rótulo do evento de sentença
    texto: str | None = None       # texto da sentença (p/ a IA)
    erro: str | None = None


@dataclass
class Coletor:
    config: dict
    cdp_url: str = CDP_URL

    def coletar_cnpjs(
        self,
        cnpjs: list[str],
        limite: int | None = None,
        prefiltrar_datajud: bool = True,
    ) -> list[ProcessoColetado]:
        out: list[ProcessoColetado] = []
        with sync_playwright() as p:
            try:
                nav = p.chromium.connect_over_cdp(self.cdp_url)
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    f"Não conectou ao Chrome via CDP ({self.cdp_url}). Abra o Chrome com "
                    f"--remote-debugging-port=9222 e resolva o Turnstile. Detalhe: {e}"
                )
            ctx = nav.contexts[0] if nav.contexts else nav.new_context()
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            for cnpj in cnpjs:
                cnpj_num = so_digitos(cnpj)
                print(f"[CNPJ] {cnpj_num}")
                try:
                    procs = self._listar(pg, cnpj_num)
                except Exception as e:  # noqa: BLE001
                    out.append(ProcessoColetado(cnpj_num, "", erro=f"lista: {e}"))
                    continue
                print(f"  {len(procs)} processos na lista")
                if prefiltrar_datajud:
                    procs = self._prefiltrar_datajud(procs)
                if limite:
                    procs = procs[:limite]
                for numero, href in procs:
                    rc = ProcessoColetado(cnpj=cnpj_num, numero_processo=numero)
                    try:
                        sent = self._sentenca(pg, href)
                        if not sent:
                            rc.erro = "sem sentença de mérito"
                        else:
                            doc_href, mov = sent
                            rc.movimento = mov
                            rc.texto = self._texto_doc(ctx, doc_href)
                            rc.nome_parte = nome_parte_ativa(rc.texto)
                    except Exception as e:  # noqa: BLE001
                        rc.erro = f"coleta: {e}"
                    out.append(rc)
            return out

    def _prefiltrar_datajud(self, procs: list[tuple[str, str]]) -> list[tuple[str, str]]:
        """Usa DataJud para abrir no eProc só processos tributários com sentença."""
        if not procs:
            return procs
        try:
            regs = datajud.consultar_varios([num for num, _ in procs])
        except Exception as e:  # noqa: BLE001
            print(f"  DataJud indisponível; seguindo sem pré-filtro: {e}")
            return procs
        if not regs:
            print("  DataJud não retornou registros; seguindo sem pré-filtro")
            return procs

        filtrados = []
        for num, href in procs:
            reg = regs.get(so_digitos(num))
            if reg and datajud.vale_coletar(reg):
                filtrados.append((num, href))
        print(f"  {len(filtrados)} candidatos após DataJud (tributário + sentença)")
        return filtrados

    # --- passos internos -------------------------------------------------
    def _listar(self, pg, cnpj: str) -> list[tuple[str, str]]:
        pg.goto(URL_LISTA.format(cnpj=cnpj), timeout=40000, wait_until="load")
        pg.wait_for_timeout(2500)
        vistos, procs = set(), []
        for a in pg.query_selector_all("a"):
            href = a.get_attribute("href") or ""
            txt = (a.inner_text() or "").strip()
            m = CNJ.search(txt) or CNJ.search(href)
            if "txtValor=" in href and m:
                num = m.group(0)
                if num not in vistos:
                    vistos.add(num)
                    if href.startswith("controlador"):
                        href = "https://consulta.trf4.jus.br/trf4/" + href
                    procs.append((num, href))
        return procs

    def _sentenca(self, pg, href: str) -> tuple[str, str] | None:
        """Abre o detalhe, expande eventos e devolve (doc_href, movimento) da sentença."""
        pg.goto(href, timeout=40000, wait_until="load")
        pg.wait_for_timeout(1500)
        for _ in range(8):
            link = pg.query_selector("text=mostrar os próximos eventos")
            if not link:
                break
            try:
                link.click()
                pg.wait_for_timeout(1000)
            except Exception:
                break
        tab = pg.query_selector("table.tabela")
        if not tab:
            return None
        # prioriza "Sentença com Resolução de Mérito"; aceita qualquer "Sentença"
        candidato = None
        for r in tab.query_selector_all("tr"):
            low = (r.inner_text() or "").lower()
            if "senten" not in low:
                continue
            a = r.query_selector("a[href*='acessar_documento']")
            if not a:
                continue
            mov = (r.inner_text() or "").strip().split("\t")
            mov = mov[2] if len(mov) > 2 else (r.inner_text() or "").strip()[:80]
            par = (a.get_attribute("href"), mov)
            if "resolu" in low and "mérito" in low or "merito" in low:
                return par  # melhor caso
            candidato = candidato or par
        return candidato

    def _texto_doc(self, ctx, doc_href: str) -> str:
        """Abre o documento e devolve o texto do frame de maior conteúdo."""
        docpg = ctx.new_page()
        try:
            docpg.goto(doc_href, timeout=40000, wait_until="load")
            docpg.wait_for_timeout(2000)
            melhor = ""
            for f in docpg.frames:
                try:
                    txt = f.evaluate("() => document.body ? document.body.innerText : ''")
                except Exception:
                    txt = ""
                txt = re.sub(r"[ \t]+", " ", txt or "").strip()
                if len(txt) > len(melhor):
                    melhor = txt
            return melhor
        finally:
            docpg.close()
