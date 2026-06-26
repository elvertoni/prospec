# Como Usar Localmente

Use apenas um arquivo:

```text
iniciar.bat
```

Ele abre um menu. Siga sempre esta ordem:

## 1. Preparar Sistema

No menu, escolha:

```text
1 - Preparar sistema
```

Isso abre:

- o painel em `http://127.0.0.1:8000`;
- o Chrome correto do TRF4, com a porta `9222`.

No painel, o login local é:

```text
usuario: admin
senha: trocar
```

## 2. Adicionar CNPJs

No painel, cole os CNPJs e clique em **Adicionar à fila**.

Exemplo:

```text
81243735000148
```

## 3. Resolver o Turnstile

No Chrome do TRF4 que abriu, resolva o Turnstile/Cloudflare se aparecer.

Deixe esse Chrome aberto.

## 4. Processar Fila

Volte ao menu e escolha:

```text
2 - Processar fila
```

Deixe a janela aberta. O agente consulta o TRF4, envia a sentença para IA e grava os prospects na planilha.

## Se Der Erro

No menu, escolha:

```text
4 - Voltar erros para pendente
```

Depois repita:

```text
1 - Preparar sistema
2 - Processar fila
```
