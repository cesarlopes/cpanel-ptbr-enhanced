# Workflow

Este documento descreve o fluxo recomendado para criar e manter uma traducao pt_BR customizada do cPanel & WHM.

## Principio central

Use sempre o arquivo original em ingles atual como fonte da verdade:

```text
locales/original/en.xlf
```

O arquivo final canonico deve seguir a estrutura, ordem, IDs, `source`, tags inline e placeholders desse arquivo. Exports pt_BR existentes sao usados como memorias de traducao, nao como estrutura principal.

## Arquivos principais

```text
locales/original/en.xlf          base canonica atual
locales/translated/pt_br.xlf     memoria de traducao 1
locales/translated/pt_br_2.xlf   memoria de traducao 2
locales/custom/*.xlf             memorias customizadas opcionais
output/pt_BR.xlf                 arquivo canonico preparado
output/pt_BR_extended.xlf        arquivo estendido com IDs extras
```

## 1. Exportar locales do WHM

No WHM, exporte:

- locale original em ingles;
- locale pt_BR oficial, se disponivel;
- locale customizado, se existir.

Salve com nomes claros. Para a base ativa, use:

```text
locales/original/en.xlf
```

Para arquivos historicos, use nomes datados:

```text
locales/original/en_2026-06-01.xlf
```

## 2. Comparar servidores ou versoes

Para comparar apenas IDs:

```bash
python scripts/compare_locale_ids.py locales/translated/pt_br.xlf locales/translated/pt_br_2.xlf
```

Para comparar conteudo por `trans-unit id`:

```bash
python scripts/compare_locales.py locales/original/en_old.xlf locales/original/en_new.xlf --json cache/diff.json
```

## 3. Preparar a primeira versao

Gere os arquivos preparados:

```bash
python scripts/prepare_v1_locale.py
```

Saidas:

```text
output/pt_BR.xlf
output/pt_BR_extended.xlf
cache/prepare_v1_report.json
```

O script:

- usa `en.xlf` como estrutura principal;
- reaproveita traducoes existentes quando sao compativeis;
- marca pendencias como `state="needs-translation"`;
- adiciona extras validos ao arquivo extended.

## 4. Validar

```bash
python scripts/validate_xlf.py output/pt_BR.xlf
python scripts/validate_xlf.py output/pt_BR_extended.xlf
```

A validacao verifica:

- XML bem formado;
- placeholders preservados;
- tags inline XLF preservadas.

## 5. Configurar OpenAI

Crie `.env` na raiz do projeto:

```env
OPENAI_API_KEY=sk-...
```

O `.env` e ignorado pelo Git.

## 6. Traduzir uma amostra

Comece pequeno:

```bash
python scripts/ai_translate_locale.py --provider openai --model gpt-5.4-mini --mode all --limit 10 --retries 2 --output output/pt_BR.ai.sample10.xlf
```

Valide:

```bash
python scripts/validate_xlf.py output/pt_BR.ai.sample10.xlf
```

## 7. Traduzir em lotes

Use limites maiores para revisar qualidade e custo:

```bash
python scripts/ai_translate_locale.py --provider openai --model gpt-5.4-mini --mode all --limit 500 --retries 2 --output output/pt_BR.ai.sample500.xlf
```

O script mostra progresso por unidade e usa cache em:

```text
cache/ai_translations.jsonl
```

### Opcao paralela com SQLite

Para lotes grandes, use `ai_translate_locale_sqlite.py`. Ele trabalha com cache SQLite, checkpoints e chamadas paralelas.

Exemplo usando xAI/Grok para continuar a traducao, mas reaproveitando primeiro as traducoes ja feitas com `gpt-5-mini` no mesmo banco:

```bash
python scripts/ai_translate_locale_sqlite.py --cache-db cache/translations.sqlite --provider xai --model grok-4-1-fast-non-reasoning --fallback-model gpt-5-mini --mode all --limit 15000 --retries 2 --checkpoint-every 250 --concurrency 3 --output output/pt_BR.grok.fast.sample15000.xlf
```

Com `--fallback-model`, o script consulta outro modelo no mesmo SQLite antes de chamar a API do provedor atual.

### Banco de revisao

Importe o original, as traducoes oficiais do cPanel e o cache de IA para as tabelas `locale_*`:

```bash
python scripts/import_locale_to_db.py --db cache/translations.sqlite --original locales/original/en.xlf --translated locales/translated/pt_br.xlf locales/translated/pt_br_2.xlf
```

Gere um resumo da cobertura:

```bash
python scripts/report_locale_db.py --db cache/translations.sqlite
```

Gere o XLF custom final:

```bash
python scripts/build_locale.py --from-db --db cache/translations.sqlite --source locales/original/en.xlf
```

O build gera `output/pt_BR.custom.xlf` e `output/pt_BR_extended.custom.xlf`. A prioridade de target e: manual revisado, IA aprovada, IA existente, cPanel valida e, por ultimo, source marcado como `needs-translation`.

## 8. Traduzir tudo

Canonico:

```bash
python scripts/ai_translate_locale.py --provider openai --model gpt-5.4-mini --mode all --retries 2 --output output/pt_BR.ai.full.xlf
```

Extended:

```bash
python scripts/ai_translate_locale.py --input output/pt_BR_extended.xlf --provider openai --model gpt-5.4-mini --mode all --retries 2 --output output/pt_BR_extended.ai.full.xlf
```

## 9. Reiniciar a traducao

Se o prompt ou glossario mudar, prefira um cache novo:

```bash
python scripts/ai_translate_locale.py --cache cache/ai_translations_v2.jsonl --provider openai --model gpt-5.4-mini --mode all --limit 100 --output output/pt_BR.ai.v2.sample100.xlf
```

Ou remova o cache antigo:

```powershell
Remove-Item cache\ai_translations.jsonl
```

## 10. Quando houver nova versao do cPanel

1. Exporte o novo `en.xlf`.
2. Salve como arquivo datado em `locales/original/`.
3. Compare com a base anterior:

```bash
python scripts/compare_locales.py locales/original/en.xlf locales/original/en_2026-06-01.xlf --json cache/diff_2026-06-01.json
```

4. Promova o novo arquivo para `locales/original/en.xlf`.
5. Rode:

```bash
python scripts/prepare_v1_locale.py
```

6. Traduza pendencias:

```bash
python scripts/ai_translate_locale.py --provider openai --model gpt-5.4-mini --mode pending --retries 2 --output output/pt_BR.ai.incremental.xlf
```

7. Valide e importe no WHM.

## 11. Importar no WHM

Use primeiro o arquivo canonico:

```text
output/pt_BR.ai.full.xlf
```

O extended deve ser tratado como experimental ate confirmar que o WHM aceita IDs extras sem efeitos colaterais:

```text
output/pt_BR_extended.ai.full.xlf
```

Depois de importar, reconstrua/recompile a base de locales no WHM conforme o procedimento do ambiente.
