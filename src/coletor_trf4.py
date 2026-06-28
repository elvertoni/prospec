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

CONFIABILIDADE: a sessão do TRF4 DEGRADA com o uso. Após resolver o Turnstile há
uma janela curta; conforme abrimos vários processos rápido, o portal passa a
servir páginas VAZIAS (sem a tabela de eventos) e depois exige o Turnstile de
novo. Por isso:
  - página vazia NÃO é tratada como "sem sentença" (seria falso negativo);
  - reabrimos o processo com espera crescente (retry) antes de desistir;
  - há um throttle entre processos para não disparar o rate-limit;
  - se várias páginas vierem vazias em sequência, ABORTAMOS com aviso para o
    humano reaquecer a sessão — em vez de varrer tudo gerando lixo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import sync_playwright

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

# parâmetros de robustez (default; sobrescrevíveis via config["trf4"])
THROTTLE_MS = 1500          # espera entre processos (anti rate-limit)
RETRIES_PROCESSO = 3        # tentativas de abrir um processo até ter a tabela
VAZIOS_SEGUIDOS_ABORTA = 3  # páginas vazias em sequência -> sessão degradou


class PaginaBloqueada(Exception):
    """Página do processo veio sem a tabela de eventos (portal bloqueou/não carregou)."""


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
    bloqueado: bool = False         # True = não verificado (portal bloqueou), reprocessar


@dataclass
class Coletor:
    config: dict
    cdp_url: str = CDP_URL

    def _cfg(self, chave: str, default):
        return (self.config.get("trf4") or {}).get(chave, default)

    def coletar_cnpjs(
        self,
        cnpjs: list[str],
        limite: int | None = None,
        on_status=None,
    ) -> list[ProcessoColetado]:
        """Varre os processos do CNPJ e abre os que têm sentença.

        `limite` corta nº de processos por CNPJ. `on_status(msg)` é callback de
        progresso. Distingue 'sem sentença' (real) de 'bloqueado' (portal não
        entregou a página — reprocessar). Aborta com SessaoDegradada se a sessão
        esfriar no meio.
        """
        throttle = int(self._cfg("throttle_ms", THROTTLE_MS))
        aborta = int(self._cfg("vazios_seguidos_aborta", VAZIOS_SEGUIDOS_ABORTA))

        def emit(msg):
            if on_status:
                on_status(msg)

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
                emit(f"Iniciando coleta para o CNPJ {cnpj_num}...")
                print(f"[CNPJ] {cnpj_num}")
                try:
                    procs = self._listar(pg, cnpj_num, on_status=on_status)
                except Exception as e:  # noqa: BLE001
                    out.append(ProcessoColetado(cnpj_num, "", erro=f"lista: {e}"))
                    continue
                print(f"  {len(procs)} processos na lista")
                emit(f"CNPJ {cnpj_num}: {len(procs)} processos encontrados.")
                if limite:
                    procs = procs[:limite]

                vazios_seguidos = 0
                for idx, (numero, href) in enumerate(procs, 1):
                    rc = ProcessoColetado(cnpj=cnpj_num, numero_processo=numero)
                    try:
                        emit(f"[{idx}/{len(procs)}] {numero}: abrindo...")
                        tab = self._abrir_eventos(pg, href)  # retry; PaginaBloqueada se vazio
                        vazios_seguidos = 0
                        sent = self._achar_sentenca(tab)
                        if not sent:
                            rc.erro = "sem sentença de mérito"
                            emit(f"[{idx}/{len(procs)}] {numero}: sem sentença de mérito.")
                        else:
                            doc_href, mov = sent
                            rc.movimento = mov
                            emit(f"[{idx}/{len(procs)}] {numero}: baixando sentença ({mov})...")
                            rc.texto = self._texto_doc(ctx, doc_href)
                            rc.nome_parte = nome_parte_ativa(rc.texto)
                    except PaginaBloqueada:
                        vazios_seguidos += 1
                        rc.bloqueado = True
                        rc.erro = "não verificado (portal bloqueou — reprocessar)"
                        emit(f"[{idx}/{len(procs)}] {numero}: ⚠️ página não carregou "
                             f"({vazios_seguidos}/{aborta}).")
                        if vazios_seguidos >= aborta:
                            out.append(rc)
                            emit("⛔ A sessão do TRF4 degradou (várias páginas vazias). "
                                 "Reaqueça: no Chrome, refaça a consulta e resolva o "
                                 "Turnstile; depois clique Coletar de novo (os já "
                                 "gravados não repetem).")
                            # interrompe preservando tudo o que já foi coletado
                            out.append(ProcessoColetado(
                                cnpj_num, "", bloqueado=True,
                                erro="SESSAO_DEGRADADA: a sessão do TRF4 esfriou no meio "
                                     "da coleta. Reaqueça o Turnstile no Chrome e clique "
                                     "Coletar de novo — os já gravados não repetem."))
                            return out
                    except Exception as e:  # noqa: BLE001
                        rc.erro = f"coleta: {e}"
                    out.append(rc)
                    pg.wait_for_timeout(throttle)
            return out

    # --- passos internos -------------------------------------------------
    def _listar(self, pg, cnpj: str, on_status=None) -> list[tuple[str, str]]:
        pg.goto(URL_LISTA.format(cnpj=cnpj), timeout=40000, wait_until="load")

        # Espera ativa: o Turnstile pode aparecer. Aguarda até a lista carregar
        # (links com nº CNJ) ou a página dizer que não há processos.
        for i in range(60):
            corpo = (pg.inner_text("body") or "").lower()
            sem_registro = "nenhum registro" in corpo or "não foram encontrados" in corpo
            tem_links = any(CNJ.search((a.inner_text() or "")) for a in pg.query_selector_all("a"))
            if tem_links or sem_registro:
                break
            # Sessão fria: o GET direto foi redirecionado para a tela de
            # formulário (acontece quando o Turnstile/PHPSESSID expira). Não
            # adianta esperar — o humano precisa refazer a busca no Chrome.
            sessao_fria = (
                "consulta_processual_pesquisa" in pg.url
                or "selecionar uma forma de pesquisa" in corpo
                or ("é necessário" in corpo and "forma de pesquisa" in corpo)
            )
            if sessao_fria and i >= 3:
                raise RuntimeError(
                    "Sessão do TRF4 expirou (ou o Turnstile não foi resolvido). "
                    "No Chrome que está aberto: faça uma consulta manual "
                    "(forma 'CPF/CNPJ da Parte', seção 'SJ Paraná'), resolva o "
                    "Turnstile e deixe a lista de processos carregar. Depois clique "
                    "Coletar de novo."
                )
            if on_status and i % 5 == 0:
                on_status("⚠️ Aguardando a lista do TRF4. Se aparecer o desafio "
                          "Cloudflare (Turnstile) no Chrome, resolva-o.")
            pg.wait_for_timeout(1000)
        else:
            raise TimeoutError("Tempo esgotado esperando o Turnstile/carregamento dos processos.")

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

    def _abrir_eventos(self, pg, href: str):
        """Abre o detalhe do processo e devolve o handle da tabela de eventos.

        Reabre com espera crescente até a tabela aparecer (o portal às vezes
        serve a página vazia sob carga). Se não vier após as tentativas, levanta
        PaginaBloqueada — para NÃO confundir com 'processo sem sentença'.
        """
        retries = int(self._cfg("retries_processo", RETRIES_PROCESSO))
        for tentativa in range(1, retries + 1):
            pg.goto(href, timeout=40000, wait_until="load")
            pg.wait_for_timeout(800 + 700 * tentativa)  # 1.5s, 2.2s, 2.9s...
            # expande todos os blocos de eventos
            for _ in range(10):
                link = pg.query_selector("text=mostrar os próximos eventos")
                if not link:
                    break
                try:
                    link.click()
                    pg.wait_for_timeout(800)
                except Exception:
                    break
            tab = pg.query_selector("table.tabela")
            if tab and len(tab.query_selector_all("tr")) > 1:
                return tab
            # página veio vazia/incompleta: detecta sessão fria explícita
            corpo = (pg.inner_text("body") or "").lower()
            if "consulta_processual_pesquisa" in pg.url or any(
                x in corpo for x in ("cloudflare", "verifique que", "sou humano")
            ):
                break  # não adianta retry — sessão fria
        raise PaginaBloqueada(href)

    @staticmethod
    def _achar_sentenca(tab) -> tuple[str, str] | None:
        """Na tabela de eventos, devolve (doc_href, movimento) da sentença, se houver."""
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
            if ("resolu" in low and "mérito" in low) or "merito" in low:
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
