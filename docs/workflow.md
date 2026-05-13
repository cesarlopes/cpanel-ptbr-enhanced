# Workflow

Este documento descreve o fluxo incremental para manter uma tradução pt_BR customizada do cPanel & WHM a partir do locale original em inglês.

## 1. Exportar locale original do WHM

No WHM, exporte o locale original em inglês em formato XLF/XLIFF 1.2.

Use nomes versionados ou datados para facilitar comparação futura, por exemplo:

```text
locales/original/en_2026-05-13.xlf
```

## 2. Salvar em `locales/original/`

Mantenha todos os arquivos originais exportados sem edição manual.

Isso permite auditar mudanças entre versões do WHM e recriar o fluxo quando necessário.

## 3. Rodar comparação

Compare o locale original anterior com o novo:

```bash
python scripts/compare_locales.py locales/original/en_old.xlf locales/original/en_new.xlf --json cache/diff.json
```

O script compara unidades de tradução por `id` e identifica:

- Strings novas.
- Strings removidas.
- Strings alteradas.

## 4. Traduzir incrementalmente

Use a tradução pt_BR customizada existente como base e aplique apenas as pendências:

```bash
python scripts/translate_incremental.py locales/original/en_new.xlf locales/custom/pt_BR.xlf --pending-json cache/diff.json --output locales/custom/pt_BR.updated.xlf
```

Neste estágio inicial, a tradução é feita por um stub offline. A estrutura do código já separa a camada de tradução para futura integração com OpenAI, Claude, Gemini ou DeepL.

## 5. Validar

Valide o arquivo XLF atualizado:

```bash
python scripts/validate_xlf.py locales/custom/pt_BR.updated.xlf
```

A validação verifica:

- XML bem formado.
- Presença de placeholders do texto original também na tradução.
- Compatibilidade básica de tags inline em `source` e `target`.

Placeholders preservados:

```text
[_1], [_2], %s, %d, {name}, {{name}}, :name
```

Entidades HTML como `&amp;`, `&lt;`, `&gt;` e `&quot;` devem permanecer válidas dentro do XML.

## 6. Gerar arquivo final em `output/`

Crie o arquivo final para importação:

```bash
python scripts/build_locale.py locales/custom/pt_BR.updated.xlf --output output/pt_BR.xlf
```

O build faz uma validação antes de copiar o arquivo para `output/`.

## 7. Importar no WHM

Importe o arquivo gerado em `output/pt_BR.xlf` no WHM usando o fluxo de locale do próprio painel.

## 8. Reconstruir a base de locales

Após importar, reconstrua/recompile a base de locales no WHM conforme o procedimento do ambiente.

Depois de validar manualmente o resultado, versione:

- O novo original em `locales/original/`.
- A tradução customizada revisada em `locales/custom/`.
- O diff em `cache/`, se fizer sentido para auditoria local.

Por padrão, `cache/` e `output/` são ignorados pelo Git, exceto seus arquivos `.gitkeep`.
