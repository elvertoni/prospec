"""Camada de persistência SQLite com máquina de estados para o sistema de fila de prospecção.

Arquivo: data/fila.sqlite (criado automaticamente em RAIZ/data/).

Tabelas:
    lotes      — agrupamento de CNPJs a prospectar
    processos  — processos individuais com máquina de estados
    eventos    — log de eventos por processo (info, erro, bloqueio, etc.)

Máquina de estados (processos.estado):
    pendente → buscando
    buscando → extraido | erro | sem_sentenca | bloqueado
    extraido → classificando
    classificando → classificado | erro_ia
    classificado → concluido
    erro / bloqueado → buscando   (retry, se tentativas < 3)
    erro_ia → classificando       (retry, se tentativas < 3)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

# ── Caminhos ───────────────────────────────────────────────────────────
RAIZ = Path(__file__).resolve().parent.parent
DB_PATH = RAIZ / "data" / "fila.sqlite"

# ── Máquina de estados ─────────────────────────────────────────────────
TRANSICOES: dict[str, set[str]] = {
    "pendente": {"buscando"},
    "buscando": {"extraido", "erro", "sem_sentenca", "bloqueado"},
    "extraido": {"classificando"},
    "classificando": {"classificado", "erro_ia"},
    "classificado": {"concluido"},
    # retry (tentativas < 3)
    "erro": {"buscando"},
    "bloqueado": {"buscando"},
    "erro_ia": {"classificando"},
}

ESTADOS_ERRO: set[str] = {"erro", "bloqueado", "erro_ia"}

# Estados que podem ser retry (retornados por proximo_pendente se tentativas < 3)
ESTADOS_RETRY: set[str] = {"erro", "bloqueado", "erro_ia"}

# Colunas da tabela processos que podem ser preenchidas via **kwargs em marcar_estado
COLUNAS_PROCESSO: set[str] = {
    "texto_sentenca",
    "trecho_tema",
    "tema_discussao",
    "descricao_completa",
    "movimento",
    "erro",
    "nome_parte",
    "numero_processo",
    "situacao",
    "patrocinador",
}

# ── Conexão thread-safe ────────────────────────────────────────────────
_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    """Retorna a conexão singleton (cria na primeira chamada)."""
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _conn.row_factory = sqlite3.Row
        _criar_tabelas(_conn)
    return _conn


def _criar_tabelas(conn: sqlite3.Connection) -> None:
    """Cria as tabelas se não existirem e aplica migrações pendentes."""
    with _lock:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS lotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                criado_em TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                status TEXT NOT NULL DEFAULT 'pendente',
                cnpjs_json TEXT NOT NULL,
                limite_por_cnpj INTEGER DEFAULT 0,
                total_processos INTEGER DEFAULT 0,
                concluidos INTEGER DEFAULT 0,
                erros INTEGER DEFAULT 0,
                pausado_motivo TEXT
            );

            CREATE TABLE IF NOT EXISTS processos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lote_id INTEGER NOT NULL REFERENCES lotes(id),
                cnpj TEXT NOT NULL,
                numero_processo TEXT NOT NULL,
                nome_parte TEXT,
                estado TEXT NOT NULL DEFAULT 'pendente',
                texto_sentenca TEXT,
                trecho_tema TEXT,
                tema_discussao TEXT,
                descricao_completa TEXT,
                movimento TEXT,
                situacao TEXT,
                patrocinador TEXT,
                erro TEXT,
                tentativas INTEGER DEFAULT 0,
                atualizado_em TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                UNIQUE(lote_id, numero_processo)
            );

            CREATE TABLE IF NOT EXISTS eventos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                processo_id INTEGER REFERENCES processos(id),
                timestamp TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                tipo TEXT NOT NULL,
                mensagem TEXT NOT NULL
            );
        """)
        _migrar_schema(conn)


def _migrar_schema(conn: sqlite3.Connection) -> None:
    """Aplica migrações incrementais no schema (idempotente).

    Chamada exclusivamente por _criar_tabelas, que já detém _lock.
    """
    for col, tipo in [
        ("descricao_completa", "TEXT"),
        ("situacao", "TEXT"),
        ("patrocinador", "TEXT"),
    ]:
        try:
            conn.execute(
                f"ALTER TABLE processos ADD COLUMN {col} {tipo}"
            )
        except sqlite3.OperationalError:
            pass  # coluna já existe


# ── Funções públicas ───────────────────────────────────────────────────

def criar_lote(cnpjs: list[str], limite: int = 0) -> int:
    """Cria um novo lote com uma lista de CNPJs.

    Os processos NÃO são inseridos aqui — são criados sob demanda pelo
    worker via inserir_processo() conforme a listagem do TRF4 retorna
    resultados.

    Args:
        cnpjs: Lista de CNPJs (só dígitos) a prospectar.
        limite: Limite de processos por CNPJ (0 = sem limite).

    Returns:
        O id do lote criado.
    """
    conn = _get_conn()
    with _lock:
        cur = conn.execute(
            "INSERT INTO lotes (cnpjs_json, limite_por_cnpj) VALUES (?, ?)",
            (json.dumps(cnpjs), limite),
        )
        conn.commit()
        return cur.lastrowid


def inserir_processo(
    lote_id: int,
    cnpj: str,
    numero_processo: str,
    nome_parte: str | None = None,
) -> int:
    """Insere um processo na tabela (chamado pelo worker após listar o TRF4).

    Args:
        lote_id: Id do lote ao qual o processo pertence.
        cnpj: CNPJ (só dígitos) da parte.
        numero_processo: Número CNJ formatado do processo.
        nome_parte: Nome da parte (opcional).

    Returns:
        O id do processo inserido.
    """
    conn = _get_conn()
    with _lock:
        try:
            cur = conn.execute(
                """INSERT INTO processos (lote_id, cnpj, numero_processo, nome_parte)
                   VALUES (?, ?, ?, ?)""",
                (lote_id, cnpj, numero_processo, nome_parte),
            )
            conn.execute(
                "UPDATE lotes SET total_processos = total_processos + 1 WHERE id = ?",
                (lote_id,),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            # UNIQUE(lote_id, numero_processo) — já existe, retorna o existente
            conn.rollback()
            cur = conn.execute(
                "SELECT id FROM processos WHERE lote_id = ? AND numero_processo = ?",
                (lote_id, numero_processo),
            )
            row = cur.fetchone()
            if row:
                return row["id"]
            raise


def lote_status(lote_id: int) -> dict:
    """Retorna um resumo do lote.

    Args:
        lote_id: Id do lote.

    Returns:
        Dict com chaves: id, status, total, concluidos, erros, motivo,
        criado_em, cnpjs.
    """
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM lotes WHERE id = ?", (lote_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Lote {lote_id} não encontrado.")
    d = dict(row)
    d["cnpjs"] = json.loads(d.pop("cnpjs_json"))
    d["motivo"] = d.pop("pausado_motivo", None)
    d["total"] = d.pop("total_processos", 0)
    return d


def lote_pausar(lote_id: int, motivo: str) -> None:
    """Pausa um lote (ex.: sessão TRF4 fria).

    Args:
        lote_id: Id do lote.
        motivo: Razão da pausa.
    """
    conn = _get_conn()
    with _lock:
        conn.execute(
            "UPDATE lotes SET status = 'pausado', pausado_motivo = ? WHERE id = ?",
            (motivo, lote_id),
        )
        conn.commit()


def lote_retomar(lote_id: int) -> None:
    """Retoma um lote pausado, colocando-o em 'em_andamento'.

    Args:
        lote_id: Id do lote.
    """
    conn = _get_conn()
    with _lock:
        conn.execute(
            "UPDATE lotes SET status = 'em_andamento', pausado_motivo = NULL WHERE id = ?",
            (lote_id,),
        )
        conn.commit()


def proximo_pendente(lote_id: int | None = None) -> dict | None:
    """Retorna o próximo processo pendente (ou em retry) mais antigo.

    A operação é atômica: o processo encontrado é imediatamente transicionado
    para 'buscando', evitando que dois workers peguem o mesmo processo.

    Só retorna processos de lotes cujo status NÃO seja 'pausado', 'concluido'
    nem 'erro'.

    Estados considerados:
        - 'pendente' (sempre)
        - 'erro', 'bloqueado', 'erro_ia' (somente se tentativas < 3)

    Args:
        lote_id: Se informado, restringe a um lote específico.

    Returns:
        Dict com todas as colunas de 'processos', ou None se nada pendente.
    """
    conn = _get_conn()
    with _lock:
        # Lotes ativos (não pausados, concluídos nem com erro)
        lote_filter = "AND p.lote_id = ?" if lote_id is not None else ""
        params: tuple = (lote_id,) if lote_id is not None else ()

        row = conn.execute(
            f"""SELECT p.* FROM processos p
                JOIN lotes l ON l.id = p.lote_id
                WHERE l.status NOT IN ('pausado', 'concluido', 'erro')
                  AND (
                      p.estado = 'pendente'
                      OR (p.estado IN ('erro', 'bloqueado', 'erro_ia') AND p.tentativas < 3)
                  )
                  {lote_filter}
                ORDER BY p.id ASC
                LIMIT 1""",
            params,
        ).fetchone()

        if row is None:
            return None

        processo = dict(row)
        pid = processo["id"]

        # Marca como 'buscando' atomicamente para evitar double-claim
        conn.execute(
            "UPDATE processos SET estado = 'buscando', atualizado_em = datetime('now','localtime') WHERE id = ?",
            (pid,),
        )
        # Garante que o lote está como 'em_andamento' (se estava 'pendente')
        conn.execute(
            "UPDATE lotes SET status = 'em_andamento' WHERE id = ? AND status = 'pendente'",
            (processo["lote_id"],),
        )
        conn.commit()

        # Recarrega para pegar o estado atualizado
        processo["estado"] = "buscando"
        return processo


def marcar_estado(processo_id: int, estado: str, **kwargs: str | None) -> None:
    """Transiciona um processo para um novo estado, validando a máquina de estados.

    Também gerencia:
        - Incremento de tentativas ao entrar em estado de erro.
        - Atualização dos contadores do lote (concluidos, erros).

    Args:
        processo_id: Id do processo.
        estado: Novo estado (precisa ser uma transição válida).
        **kwargs: Campos adicionais para atualizar na linha (ex.: texto_sentenca,
                  trecho_tema, tema_discussao, erro, nome_parte, numero_processo).

    Raises:
        ValueError: Se o processo não existe ou se a transição é inválida.
    """
    conn = _get_conn()
    with _lock:
        # Lê estado atual
        row = conn.execute(
            "SELECT estado, lote_id FROM processos WHERE id = ?",
            (processo_id,),
        ).fetchone()

        if row is None:
            raise ValueError(f"Processo {processo_id} não encontrado.")

        atual = row["estado"]
        lote_id = row["lote_id"]

        # Valida transição
        permitidas = TRANSICOES.get(atual, set())
        if estado not in permitidas:
            raise ValueError(
                f"Transição inválida: '{atual}' → '{estado}'. "
                f"Permitidas: {sorted(permitidas)}"
            )

        # Filtra apenas colunas válidas da tabela processos
        campos = {k: v for k, v in kwargs.items() if k in COLUNAS_PROCESSO}

        # Monta SET da query
        sets = ["estado = ?", "atualizado_em = datetime('now','localtime')"]
        params: list = [estado]

        for col, val in campos.items():
            sets.append(f"{col} = ?")
            params.append(val)

        # Incrementa tentativas ao entrar em estado de erro
        if estado in ESTADOS_ERRO:
            sets.append("tentativas = tentativas + 1")

        params.append(processo_id)
        conn.execute(
            f"UPDATE processos SET {', '.join(sets)} WHERE id = ?",
            params,
        )

        # Atualiza contadores do lote
        if estado == "concluido":
            conn.execute(
                "UPDATE lotes SET concluidos = concluidos + 1 WHERE id = ?",
                (lote_id,),
            )
        elif estado == "erro":
            conn.execute(
                "UPDATE lotes SET erros = erros + 1 WHERE id = ?",
                (lote_id,),
            )

        conn.commit()


def marcar_concluido(
    processo_id: int,
    nome: str,
    numero: str,
    tema: str,
    situacao: str = "",
    patrocinador: str = "",
    descricao_completa: str = "",
) -> None:
    """Atalho para marcar um processo como concluído com todos os campos.

    Equivale a:
        marcar_estado(processo_id, 'concluido', nome_parte=nome,
                      numero_processo=numero, tema_discussao=tema,
                      situacao=situacao, patrocinador=patrocinador,
                      descricao_completa=descricao_completa)

    Args:
        processo_id: Id do processo.
        nome: Nome da parte.
        numero: Número CNJ do processo.
        tema: Tema da discussão classificado pela IA.
        situacao: Situação processual (ex: 'BAIXADO', 'ATIVO').
        patrocinador: Nome do advogado patrocinador.
        descricao_completa: Fallback: parágrafo completo quando IA incerta.
    """
    marcar_estado(
        processo_id,
        "concluido",
        nome_parte=nome,
        numero_processo=numero,
        tema_discussao=tema,
        situacao=situacao,
        patrocinador=patrocinador,
        descricao_completa=descricao_completa,
    )


def registrar_evento(processo_id: int, tipo: str, mensagem: str) -> None:
    """Registra um evento no log do processo.

    Args:
        processo_id: Id do processo (opcional — use 0 ou NULL se não vinculado).
        tipo: Tipo do evento (info, erro, bloqueio, sessao_fria, classificacao).
        mensagem: Descrição do evento.
    """
    conn = _get_conn()
    with _lock:
        conn.execute(
            "INSERT INTO eventos (processo_id, tipo, mensagem) VALUES (?, ?, ?)",
            (processo_id, tipo, mensagem),
        )
        conn.commit()


def listar_lotes(ativos: bool = True) -> list[dict]:
    """Lista todos os lotes, opcionalmente filtrando apenas os ativos.

    Args:
        ativos: Se True (padrão), retorna apenas lotes cujo status NÃO seja
                'concluido' nem 'erro'.

    Returns:
        Lista de dicts com todas as colunas de 'lotes'.
    """
    conn = _get_conn()
    if ativos:
        rows = conn.execute(
            "SELECT * FROM lotes WHERE status NOT IN ('concluido', 'erro') ORDER BY id DESC"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM lotes ORDER BY id DESC"
        ).fetchall()

    resultado: list[dict] = []
    for row in rows:
        d = dict(row)
        d["cnpjs"] = json.loads(d.pop("cnpjs_json"))
        d["motivo"] = d.pop("pausado_motivo", None)
        d["total"] = d.pop("total_processos", 0)
        resultado.append(d)
    return resultado
