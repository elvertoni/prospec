# Prospec 3.0 — Spec de Arquitetura
# Contrato para implementação multi-agente.
# Baseado no documento "Agente de Prospecção.docx" de Toni Coimbra.

## Objetivo

Expandir o pipeline de prospecção tributária TRF4 de 3 colunas para 5 colunas,
com classificação de tema mais específica e extração de situação processual e
nome do advogado patrocinador.

## Schema — Tabela `processos` (SQLite)

Novas colunas a adicionar (ALTER TABLE ou CREATE TABLE com novas colunas):

```sql
ALTER TABLE processos ADD COLUMN situacao TEXT;       -- ex: 'BAIXADO', 'ATIVO', 'SUSPENSO'
ALTER TABLE processos ADD COLUMN patrocinador TEXT;   -- ex: 'LEONARDO SPERB DE PAOLA'
ALTER TABLE processos ADD COLUMN descricao_completa TEXT;  -- fallback: parágrafo quando IA incerta
```

> ⚠️ Se o SQLite já tiver dados, usar ALTER TABLE. Se for recriar, incluir no CREATE TABLE.

Adicionar `situacao`, `patrocinador`, `descricao_completa` ao set `COLUNAS_PROCESSO`.

## Máquina de Estados

**Sem alteração** nas transições. A máquina continua idêntica. Os novos campos
são preenchidos durante o fluxo normal (não criam novos estados).

## Interfaces — Funções públicas

### `src/sessao.py` — SessionManager

```python
# ALTERADO: retorna (numero, href, situacao) em vez de (numero, href)
def listar_processos(self, cnpj: str) -> list[tuple[str, str, str]]:
    """Retorna lista de (numero_cnj, href, situacao).
    situacao é o texto da coluna de status na listagem do eProc
    (ex: 'BAIXADO', 'ATIVO', 'SUSPENSO', '' se não encontrado)."""

# NOVA função standalone
def nome_patrocinador(texto: str) -> str | None:
    """Extrai nome do advogado/patrocinador do texto da sentença.
    Procura por padrões como 'ADVOGADO: NOME', 'ADVOGADA: NOME',
    'PATROCINADO POR: NOME'. Retorna None se não encontrar."""
```

### `src/fila.py` — Persistência

```python
# ALTERADO: aceita situacao, patrocinador, descricao_completa
def marcar_concluido(
    processo_id: int,
    nome: str,
    numero: str,
    tema: str,
    situacao: str = "",
    patrocinador: str = "",
    descricao_completa: str = "",
) -> None:
    """Atalho para marcar processo como concluído com todos os campos."""

# ALTERADO: kwargs aceita situacao, patrocinador, descricao_completa
def marcar_estado(processo_id: int, estado: str, **kwargs) -> None:
    """COLUNAS_PROCESSO atualizado para incluir situacao, patrocinador,
    descricao_completa."""

# INALTERADO
def criar_lote(...) -> int
def inserir_processo(...) -> int
def proximo_pendente(...) -> dict | None
def lote_status(...) -> dict
def lote_pausar(...) -> None
def lote_retomar(...) -> None
def registrar_evento(...) -> None
def listar_lotes(...) -> list[dict]
```

### `src/sheets.py` — Google Sheets

```python
# ALTERADO: cabeçalho de 3 para 5 colunas
CABECALHO = [
    "NOME CLIENTE",
    "NUMERO DO PROCESSO",
    "TEMA DA DISCUSSÃO",
    "SITUAÇÃO",
    "PATROCINADOR",
]

# ALTERADO: assinatura estendida
def gravar(
    nome_cliente: str,
    numero_processo: str,
    tema: str,
    situacao: str = "",
    patrocinador: str = "",
    ws=None,
) -> None:
    """Append de uma linha [nome, numero, tema, situacao, patrocinador]."""

# INALTERADO
def numeros_ja_gravados(ws=None) -> set[str]
```

### `src/prompt.xml` — Classificador

**Reescrita completa.** O novo prompt deve:

1. **Persona**: Assistente jurídico-tributário que extrai a tese completa da discussão.
2. **Tarefa**: Devolver a discussão tributária de forma completa e específica.
   - NUNCA responder só o tributo (ex.: "PIS/COFINS" é insuficiente).
   - SEMPRE incluir o objeto específico da discussão (ex.: "PIS/COFINS sobre taxa de franquia").
   - Quando o texto descrever claramente a tese, usar formato: "TRIBUTO sobre MOTIVO".
   - Se a tese for complexa, descrevê-la em até 15 palavras.
   - Se não for possível identificar a tese específica, devolver o parágrafo completo como `descricao_completa`.
3. **Formato de saída**: JSON com dois campos:
   ```json
   {
     "tema_discussao": "PIS/COFINS sobre taxa de franquia",
     "descricao_completa": ""
   }
   ```
   - `tema_discussao`: string vazia se incerto → nesse caso preencher `descricao_completa`.
   - `descricao_completa`: parágrafo original com a discussão quando a IA não consegue resumir com precisão.
4. **Regras**:
   - Basear-se EXCLUSIVAMENTE no texto. Não inventar fatos.
   - Se não for matéria tributária: `tema_discussao = "NÃO TRIBUTÁRIO"`.
   - Se nem o parágrafo ajudar: `tema_discussao = "NÃO IDENTIFICADO"`.
5. **Exemplos**:
   - Entrada: "Trata-se de ação ajuizada pela autora objetivando o direito de utilizar como crédito os valores referentes ao PIS e a COFINS que incidiram sobre a taxa de franquia."
   - Saída: `{"tema_discussao": "PIS/COFINS sobre taxa de franquia", "descricao_completa": ""}`
   - Entrada: "A parte impetrante discute a incidência de contribuição previdenciária sobre o terço constitucional de férias."
   - Saída: `{"tema_discussao": "Contribuição Previdenciária sobre terço constitucional de férias", "descricao_completa": ""}`

### `src/classificador.py` — Adaptação

```python
# ALTERADO: retorna dict em vez de str
def classificar_tema(
    texto: str,
    *,
    numero_processo: str = "",
    ...
) -> dict:
    """Devolve {'tema_discussao': str, 'descricao_completa': str}."""

# ALTERADO: _extrair_tema agora extrai dois campos
def _extrair_tema(raw: str | None) -> dict:
    """Extrai {'tema_discussao': ..., 'descricao_completa': ...} do JSON."""
```

## Arquivos a criar/modificar

| Arquivo | Ação | Agente | Descrição |
|---|---|---|---|
| `src/prompt.xml` | MODIFICAR | A | Prompt completo e específico |
| `src/classificador.py` | MODIFICAR | A | Adaptar para retornar dict com 2 campos |
| `src/sheets.py` | MODIFICAR | A | 3→5 colunas |
| `src/sessao.py` | MODIFICAR | B | Extrair situação + patrocinador |
| `src/fila.py` | MODIFICAR | C | Schema + novos campos no COLUNAS_PROCESSO |
| `worker.py` | MODIFICAR | Orquestrador | Integrar todos os novos campos |
| `app.py` | MODIFICAR | Orquestrador | Mostrar novas colunas no dashboard |

## Invariantes

1. **Nunca quebrar compatibilidade** com `data/fila.sqlite` existente — usar ALTER TABLE, não DROP/CREATE.
2. **Nomes de colunas exatos**: `situacao`, `patrocinador`, `descricao_completa` — sem variantes.
3. **A API do Sheets mantém `numeros_ja_gravados()` inalterada** — dedup por número do processo.
4. **Ordem das colunas no Sheets**: NOME CLIENTE | NUMERO DO PROCESSO | TEMA DA DISCUSSÃO | SITUAÇÃO | PATROCINADOR.
5. **Prompt XML mantém estrutura XML** (lido por `_system_instruction()` que espera ler o arquivo inteiro).
6. **`listar_processos()` mantém compatibilidade**: tupla de 3 elementos (numero, href, situacao). O worker.py existente que usa `for numero, href in procs` precisa ser atualizado.
7. **Não commitar** — deixar diffs prontos para revisão do Toni.
