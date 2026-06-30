"""Session Manager para Chrome CDP no TRF4 (eProc).

Encapsula toda a lógica de conexão CDP que hoje está em coletor_trf4.py,
fornecendo uma interface limpa para o worker desacoplado.

Responsabilidades:
  - conectar() / fechar(): gerencia o ciclo de vida do Playwright CDP
  - health_check(): verifica se a sessão TRF4 está saudável
  - aquecer(): navega e espera o Turnstile/Cloudflare ser resolvido
  - listar_processos(): extrai links CNJ da lista de consulta
  - abrir_processo(): abre detalhe do processo com retry
  - achar_sentenca(): localiza o documento da sentença na tabela
  - texto_documento(): extrai innerText do documento

Exceções:
  - SessaoFria: Turnstile/PHPSESSID expirou — requer intervenção humana
  - SessaoNaoConectada: operação chamada antes de conectar()
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from .util import CNJ

# Constantes (mantidas compatíveis com coletor_trf4.py)
URL_LISTA = (
    "https://consulta.trf4.jus.br/trf4/controlador.php?"
    "acao=consulta_processual_valida_pesquisa&selForma=CP"
    "&txtValor={cnpj}&selOrigem=PR&chkMostrarBaixados=S&txtOrigemPesquisa=1"
)

URL_BASE = (
    "https://consulta.trf4.jus.br/trf4/controlador.php?"
    "acao=consulta_processual_pesquisa"
)

# Rótulos do polo ativo no cabeçalho da sentença (eProc)
_POLO_ATIVO = (
    r"(?:AUTOR(?:A)?|IMPETRANTE|REQUERENTE|EXEQUENTE|EMBARGANTE|AGRAVANTE|APELANTE)"
)
_RE_NOME = re.compile(
    _POLO_ATIVO
    + r"\s*:\s*(.+?)\s*(?:ADVOGAD|IMPETRAD|R[ÉE]U|REQUERID|EXECUTAD|MPF|MINIST[ÉE]RIO|\n)",
    re.I,
)

# Parâmetros de robustez (default; sobrescrevíveis via config["trf4"])
THROTTLE_MS = 1500          # espera entre processos (anti rate-limit)
RETRIES_PROCESSO = 3        # tentativas de abrir um processo até ter a tabela
VAZIOS_SEGUIDOS_ABORTA = 3  # páginas vazias em sequência -> sessão degradou


class SessaoFria(Exception):
    """Sessão do TRF4 expirou (Turnstile/PHPSESSID). Precisa reaquecer."""


class SessaoNaoConectada(Exception):
    """Não há conexão ativa com o Chrome CDP."""


def nome_parte_ativa(texto: str) -> str | None:
    """Extrai o nome do polo ativo do cabeçalho da sentença, se houver."""
    m = _RE_NOME.search(texto or "")
    if m:
        nome = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
        return nome[:120] or None
    return None


# Regex para extrair nome do advogado/patrocinador do texto da sentença
_RE_PATROCINADOR = re.compile(
    r"(?:ADVOGAD[OA]S?\s*:\s*|PATROCINAD[OA]\s+POR\s*:\s*)"
    r"(.+?)(?:\s*(?:"
    r"R[ÉE]U|IMPETRAD|REQUERID|EXECUTAD|MPF|MINIST[ÉE]RIO|ADVOGAD"
    r"|\s+E\s+"
    r"|$))",
    re.I,
)


def nome_patrocinador(texto: str) -> str | None:
    """Extrai nome do advogado/patrocinador do texto da sentença.

    Procura por padrões como 'ADVOGADO: NOME', 'ADVOGADA: NOME',
    'PATROCINADO POR: NOME' e extrai o nome até o próximo campo.
    Retorna None se não encontrar.
    """
    m = _RE_PATROCINADOR.search(texto or "")
    if m:
        nome = re.sub(r"\s+", " ", m.group(1)).strip(" .:-")
        # Remove trailing junk like "E ", " E" from multi-lawyer listings
        nome = re.sub(r"\s*[Ee]\s*$", "", nome).strip()
        return nome[:120] or None
    return None


class SessionManager:
    """Gerencia a sessão CDP do Chrome para navegação no TRF4 eProc."""

    def __init__(
        self,
        cdp_url: str = "http://127.0.0.1:9222",
        config: dict | None = None,
    ):
        self.cdp_url = cdp_url
        self.config = config or {}
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None

    # ------------------------------------------------------------------
    # helpers internos
    # ------------------------------------------------------------------

    def _cfg(self, chave: str, default):
        """Lê parâmetro de config['trf4'] com fallback."""
        return (self.config.get("trf4") or {}).get(chave, default)

    def _assert_conectado(self):
        """Levanta SessaoNaoConectada se não houver página ativa."""
        if not self._page:
            raise SessaoNaoConectada(
                "Nenhuma conexão CDP ativa. Chame conectar() primeiro."
            )

    def _detecta_frio(self, corpo: str, url: str) -> bool:
        """Heurística de sessão fria: redirecionado para formulário."""
        return (
            "consulta_processual_pesquisa" in url
            or "selecionar uma forma de pesquisa" in corpo
            or ("é necessário" in corpo and "forma de pesquisa" in corpo)
        )

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------

    def conectar(self) -> bool:
        """Anexa ao Chrome CDP. Retorna True se conectou com sucesso."""
        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else self._browser.new_context()
            )
            self._page = (
                self._context.pages[0]
                if self._context.pages
                else self._context.new_page()
            )
            return True
        except Exception:
            self._playwright = None
            self._browser = None
            self._context = None
            self._page = None
            return False

    def health_check(self) -> bool:
        """Navega para a página inicial do TRF4 e verifica se não foi
        redirecionado para o formulário de pesquisa (sinal de sessão fria).

        Retorna True se a sessão está saudável (página carregou sem
        redirecionamento para formulário).
        """
        self._assert_conectado()
        try:
            self._page.goto(URL_BASE, timeout=30000, wait_until="load")
            if "consulta_processual_pesquisa" in self._page.url:
                return False
            return True
        except Exception:
            return False

    def esta_frio(self) -> bool:
        """Verifica se a página atual indica sessão fria (redirecionado
        para formulário de pesquisa)."""
        self._assert_conectado()
        try:
            corpo = (self._page.inner_text("body") or "").lower()
            return self._detecta_frio(corpo, self._page.url)
        except Exception:
            # Se não consegue verificar, assume fria
            return True

    def aquecer(self, on_status=None) -> bool:
        """Navega para a consulta do TRF4 e espera o Turnstile/Cloudflare
        ser resolvido pelo humano no Chrome.

        Usa um CNPJ genérico para disparar a página de consulta e aguarda
        até que a lista carregue (ou apareça 'nenhum registro'). Se a
        sessão estiver fria e não resolver em ~3s, levanta SessaoFria.

        Retorna True quando a sessão está quente e pronta para uso.
        """
        self._assert_conectado()

        def emit(msg):
            if on_status:
                on_status(msg)

        # CNPJ genérico apenas para disparar a renderização da página
        self._page.goto(
            URL_LISTA.format(cnpj="00000000000191"),
            timeout=40000,
            wait_until="load",
        )

        for i in range(60):
            corpo = (self._page.inner_text("body") or "").lower()
            sem_registro = (
                "nenhum registro" in corpo
                or "não foram encontrados" in corpo
            )
            tem_links = any(
                CNJ.search((a.inner_text() or ""))
                for a in self._page.query_selector_all("a")
            )

            if tem_links or sem_registro:
                return True

            if self._detecta_frio(corpo, self._page.url) and i >= 3:
                raise SessaoFria(
                    "Sessão do TRF4 expirou (ou o Turnstile não foi resolvido). "
                    "No Chrome que está aberto: faça uma consulta manual "
                    "(forma 'CPF/CNPJ da Parte', seção 'SJ Paraná'), resolva o "
                    "Turnstile e deixe a lista de processos carregar. Depois "
                    "reinicie a coleta."
                )

            if on_status and i % 5 == 0:
                emit(
                    "⚠️ Aguardando o Turnstile/Cloudflare no Chrome. "
                    "Se aparecer o desafio, resolva-o."
                )

            self._page.wait_for_timeout(1000)

        raise TimeoutError(
            "Tempo esgotado esperando o Turnstile/carregamento dos processos."
        )

    def listar_processos(
        self, cnpj: str, on_status=None
    ) -> list[tuple[str, str, str]]:
        """Lista processos do CNPJ na consulta do TRF4.

        Navega para a URL de consulta, espera o Turnstile (até 60s),
        detecta sessão fria e extrai os links com número CNJ e situação.

        Retorna lista de tuplas (numero_cnj, href, situacao).
        situacao é o status do processo na listagem (ex: 'BAIXADO', 'ATIVO').
        """
        self._assert_conectado()

        def emit(msg):
            if on_status:
                on_status(msg)

        self._page.goto(
            URL_LISTA.format(cnpj=cnpj),
            timeout=40000,
            wait_until="load",
        )

        # Espera ativa: Turnstile pode aparecer. Aguarda até a lista
        # carregar (links CNJ) ou página informar que não há processos.
        for i in range(60):
            corpo = (self._page.inner_text("body") or "").lower()
            sem_registro = (
                "nenhum registro" in corpo
                or "não foram encontrados" in corpo
            )
            tem_links = any(
                CNJ.search((a.inner_text() or ""))
                for a in self._page.query_selector_all("a")
            )

            if tem_links or sem_registro:
                break

            if self._detecta_frio(corpo, self._page.url) and i >= 3:
                raise SessaoFria(
                    "Sessão do TRF4 expirou (ou o Turnstile não foi resolvido). "
                    "No Chrome: refaça a consulta manual, resolva o Turnstile "
                    "e deixe a lista de processos carregar antes de continuar."
                )

            if on_status and i % 5 == 0:
                emit(
                    "⚠️ Aguardando a lista do TRF4. "
                    "Se aparecer o desafio Cloudflare (Turnstile) no Chrome, "
                    "resolva-o."
                )

            self._page.wait_for_timeout(1000)
        else:
            raise TimeoutError(
                "Tempo esgotado esperando o Turnstile/carregamento "
                "dos processos."
            )

        # Extrai links com números CNJ e situação processual
        vistos: set[str] = set()
        procs: list[tuple[str, str, str]] = []

        for tr in self._page.query_selector_all("tr"):
            link = tr.query_selector("a[href*='txtValor=']")
            if not link:
                continue
            href = link.get_attribute("href") or ""
            txt_link = (link.inner_text() or "").strip()
            m = CNJ.search(txt_link) or CNJ.search(href)
            if not m:
                continue

            num = m.group(0)
            if num in vistos:
                continue
            vistos.add(num)

            if href.startswith("controlador"):
                href = "https://consulta.trf4.jus.br/trf4/" + href

            # Extrai situação: procura por padrões de status na linha da tabela
            situacao = ""
            row_text = (tr.inner_text() or "").upper()
            for padrao in [
                r"\b(BAIXADO)\b", r"\b(ARQUIVADO)\b", r"\b(ATIVO)\b",
                r"\b(SUSPENSO)\b", r"\b(EXTINTO)\b",
                r"\b(TRÂNSITO\s+EM\s+JULGADO)\b",
                r"\b(TRANSITADO\s+EM\s+JULGADO)\b",
                r"\b(SENTENÇA)\b", r"\b(SENTENCA)\b",
                r"\b(DECISÃO)\b", r"\b(DECISAO)\b",
                r"\b(AGUARDANDO)\b", r"\b(CONCLUSO)\b",
                r"\b(REMETIDO)\b", r"\b(DISTRIBUÍDO)\b",
                r"\b(JULGADO)\b", r"\b(TRANSITADO)\b",
            ]:
                match = re.search(padrao, row_text)
                if match:
                    situacao = match.group(1).upper().replace("\n", " ")
                    break

            procs.append((num, href, situacao))

        return procs

    def abrir_processo(self, href: str):
        """Abre o detalhe do processo e devolve o handle da tabela de eventos.

        Reabre com espera crescente (até RETRIES_PROCESSO tentativas) até
        a tabela aparecer. Expande todos os blocos de 'mostrar os próximos
        eventos' antes de retornar.

        Levanta SessaoFria se:
          - A sessão foi redirecionada para formulário (fria de fato)
          - Apareceu Cloudflare/Turnstile no meio
          - A tabela não carregou após todas as tentativas
        """
        self._assert_conectado()

        retries = int(self._cfg("retries_processo", RETRIES_PROCESSO))

        for tentativa in range(1, retries + 1):
            self._page.goto(href, timeout=40000, wait_until="load")
            self._page.wait_for_timeout(800 + 700 * tentativa)  # 1.5s, 2.2s, 2.9s...

            # Expande todos os blocos de eventos
            for _ in range(10):
                link = self._page.query_selector(
                    "text=mostrar os próximos eventos"
                )
                if not link:
                    break
                try:
                    link.click()
                    self._page.wait_for_timeout(800)
                except Exception:
                    break

            tab = self._page.query_selector("table.tabela")
            if tab and len(tab.query_selector_all("tr")) > 1:
                return tab

            # Detecta sessão fria explícita (não adianta retry)
            corpo = (self._page.inner_text("body") or "").lower()
            if "consulta_processual_pesquisa" in self._page.url or any(
                x in corpo
                for x in ("cloudflare", "verifique que", "sou humano")
            ):
                raise SessaoFria(
                    "Sessão do TRF4 expirou durante abertura do processo. "
                    "Reaqueça o Turnstile no Chrome e tente novamente."
                )

        # Esgotou as tentativas sem carregar a tabela
        raise SessaoFria(
            f"Página do processo não carregou a tabela de eventos "
            f"após {retries} tentativas. A sessão pode estar degradada. "
            f"Reaqueça o Turnstile no Chrome."
        )

    @staticmethod
    def achar_sentenca(tab) -> tuple[str, str] | None:
        """Na tabela de eventos, devolve (doc_href, movimento) da sentença,
        se houver.

        Prioriza sentenças com 'resolveu o mérito'/'julgou o mérito'.
        Caso contrário, retorna a primeira que contenha 'senten'.
        Retorna None se não houver sentença na tabela.
        """
        candidato = None
        for r in tab.query_selector_all("tr"):
            low = (r.inner_text() or "").lower()
            if "senten" not in low:
                continue
            a = r.query_selector("a[href*='acessar_documento']")
            if not a:
                continue
            mov = (r.inner_text() or "").strip().split("\t")
            mov = (
                mov[2]
                if len(mov) > 2
                else (r.inner_text() or "").strip()[:80]
            )
            par = (a.get_attribute("href"), mov)
            if ("resolu" in low and "mérito" in low) or "merito" in low:
                return par  # melhor caso
            candidato = candidato or par
        return candidato

    def texto_documento(self, doc_href: str) -> str:
        """Abre o documento (sentença) e devolve o innerText do frame
        de maior conteúdo.

        Abre em uma nova aba, extrai o texto de todos os frames e
        retorna o maior (documento principal, ignorando cabeçalhos/rodapés).
        """
        self._assert_conectado()

        docpg = self._context.new_page()
        try:
            docpg.goto(doc_href, timeout=40000, wait_until="load")
            docpg.wait_for_timeout(2000)
            melhor = ""
            for f in docpg.frames:
                try:
                    txt = f.evaluate(
                        "() => document.body ? document.body.innerText : ''"
                    )
                except Exception:
                    txt = ""
                txt = re.sub(r"[ \t]+", " ", txt or "").strip()
                if len(txt) > len(melhor):
                    melhor = txt
            return melhor
        finally:
            docpg.close()

    def fechar(self) -> None:
        """Fecha a conexão Playwright e libera recursos.

        A aba do Chrome permanece aberta (é o browser do humano).
        Apenas a conexão CDP é encerrada.
        """
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                pass
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
