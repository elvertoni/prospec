# Como Usar (João)

Um arquivo só: **`iniciar.bat`** (duplo clique).

Ele faz tudo: fecha o Chrome, reabre o Chrome certo (com a porta de depuração) e
abre o painel no navegador.

## Passo a passo

1. **Duplo clique em `iniciar.bat`.**

2. No **Chrome** que abriu, faça a consulta de um CNPJ no TRF4 e **resolva o
   Turnstile** (o "não sou robô" da Cloudflare), se aparecer. Deixe essa janela
   aberta.

3. No **painel** (abriu no navegador), cole os CNPJs — um por linha, só números:

   ```
   81243735000148
   ```

4. Clique **▶ Coletar**. Espere. O sistema entra no TRF4, lê as sentenças,
   descobre o tema e grava na planilha. As linhas novas aparecem na tela.

## Dicas

- **Limite por CNPJ**: deixe `0` para varrer todos os processos. Coloque um número
  (ex.: `5`) só para testar rápido.
- Se der erro de conexão, confirme que o **Chrome da etapa 2 está aberto** e que o
  Turnstile foi resolvido. Depois clique **Coletar** de novo.
- Processos que já estão na planilha não são gravados de novo (sem duplicar).
