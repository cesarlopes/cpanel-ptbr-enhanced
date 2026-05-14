# cpanel-ptbr-enhanced

Fluxo versionado para melhorar a traducao pt_BR do cPanel & WHM a partir de arquivos XLF/XLIFF 1.2 exportados pelo WHM.

Este projeto nao e oficial da cPanel, LLC. Ele e um fluxo customizado para revisar, padronizar e evoluir traducoes pt_BR com controle de versao.

## Objetivo

- Manter o locale original em ingles versionado.
- Usar o `en.xlf` atual como fonte da verdade.
- Reaproveitar traducoes existentes de `pt_br.xlf`, `pt_br_2.xlf` e exports customizados.
- Validar placeholders, tags inline XLF, entidades XML/HTML e estrutura.
- Preparar um arquivo canonico para a versao atual do WHM.
- Preparar um arquivo estendido com IDs extras encontrados em outros exports.
- Permitir revisao/traducao por IA com cache incremental.

## Requisitos

- Python 3.11+ recomendado.
- Arquivos `.xlf` ou `.xliff` exportados do WHM.
- Biblioteca `openai` apenas se for usar traducao via OpenAI.

Instale dependencias opcionais:

```bash
pip install -r requirements.txt
```

## Estrutura

```text
locales/
  original/      arquivos originais em ingles exportados do WHM
  translated/    traducoes pt_BR oficiais/base exportadas do WHM
  custom/        traducoes pt_BR customizadas/revisadas
glossary/        termos e frases fixas
scripts/         ferramentas de comparacao, preparo, validacao e traducao
cache/           cache local e relatorios temporarios
output/          arquivos finais gerados
docs/            documentacao do fluxo
```

## Fluxo recomendado para a primeira versao

1. Exporte do WHM:

```text
locales/original/en.xlf
locales/translated/pt_br.xlf
locales/translated/pt_br_2.xlf
```

2. Prepare os arquivos base:

```bash
python scripts/prepare_v1_locale.py
```

Isso gera:

```text
output/pt_BR.xlf
output/pt_BR_extended.xlf
cache/prepare_v1_report.json
```

`output/pt_BR.xlf` usa `en.xlf` como fonte da verdade. `output/pt_BR_extended.xlf` adiciona unidades extras validas encontradas nas memorias de traducao.

3. Valide:

```bash
python scripts/validate_xlf.py output/pt_BR.xlf
python scripts/validate_xlf.py output/pt_BR_extended.xlf
```

4. Configure a chave da OpenAI em `.env`:

```env
OPENAI_API_KEY=sk-...
```

5. Teste uma amostra pequena:

```bash
python scripts/ai_translate_locale.py --provider openai --model gpt-5.4-mini --mode all --limit 10 --retries 2 --output output/pt_BR.ai.sample10.xlf
```

6. Valide a amostra:

```bash
python scripts/validate_xlf.py output/pt_BR.ai.sample10.xlf
```

7. Rode uma leva maior:

```bash
python scripts/ai_translate_locale.py --provider openai --model gpt-5.4-mini --mode all --limit 500 --retries 2 --output output/pt_BR.ai.sample500.xlf
```

8. Quando estiver satisfeito, rode o canonico completo:

```bash
python scripts/ai_translate_locale.py --provider openai --model gpt-5.4-mini --mode all --retries 2 --output output/pt_BR.ai.full.xlf
```

9. Para traduzir o arquivo estendido:

```bash
python scripts/ai_translate_locale.py --input output/pt_BR_extended.xlf --provider openai --model gpt-5.4-mini --mode all --retries 2 --output output/pt_BR_extended.ai.full.xlf
```

O cache em `cache/ai_translations.jsonl` reaproveita traducoes entre o canonico e o extended quando `id`, `source`, modelo e glossario forem iguais.

### Traducao paralela com SQLite e multiplos provedores

Para execucoes longas, prefira o script SQLite paralelo. Ele evita corromper cache em execucoes concorrentes e permite reaproveitar traducoes feitas por outro modelo antes de chamar uma nova API.

Configure as chaves necessarias em `.env`:

```env
OPENAI_API_KEY=sk-...
XAI_API_KEY=xai-...
```

Exemplo para continuar com xAI/Grok, reaproveitando primeiro tudo que ja foi traduzido com `gpt-5-mini` no mesmo banco SQLite:

```bash
python scripts/ai_translate_locale_sqlite.py --cache-db cache/translations.sqlite --provider xai --model grok-4-1-fast-non-reasoning --fallback-model gpt-5-mini --mode all --limit 15000 --retries 2 --checkpoint-every 250 --concurrency 3 --output output/pt_BR.grok.fast.sample15000.xlf
```

Nesse modo, a ordem e:

1. usar cache do modelo atual;
2. usar cache do `--fallback-model` no mesmo SQLite;
3. usar caches informados em `--fallback-cache-db`, se existirem;
4. chamar a API somente para o que ainda faltar.

### Banco de revisao e build custom

Depois de importar os arquivos do WHM e/ou gerar traducoes por IA, carregue tudo para as tabelas de revisao no SQLite:

```bash
python scripts/import_locale_to_db.py --db cache/translations.sqlite --original locales/original/en.xlf --translated locales/translated/pt_br.xlf locales/translated/pt_br_2.xlf
```

Veja a cobertura atual:

```bash
python scripts/report_locale_db.py --db cache/translations.sqlite
```

Gere os arquivos finais a partir do banco:

```bash
python scripts/build_locale.py --from-db --db cache/translations.sqlite --source locales/original/en.xlf
```

Os arquivos gerados sao:

```text
output/pt_BR.custom.xlf
output/pt_BR_extended.custom.xlf
```

Valide antes de importar no WHM:

```bash
python scripts/validate_xlf.py output/pt_BR_extended.custom.xlf
python scripts/qa_translations.py output/pt_BR_extended.custom.xlf --json cache/qa_extended_custom.json --markdown cache/qa_extended_custom.md
```

## Reiniciar a traducao por IA

Se o prompt, glossario ou qualidade desejada mudar bastante, use um cache novo ou remova o cache antigo:

```bash
del cache\ai_translations.jsonl
```

No PowerShell:

```powershell
Remove-Item cache\ai_translations.jsonl
```

Tambem e possivel manter caches separados:

```bash
python scripts/ai_translate_locale.py --cache cache/ai_translations_v2.jsonl --provider openai --model gpt-5.4-mini --mode all --limit 100 --output output/pt_BR.ai.v2.sample100.xlf
```

## Quando o cPanel gerar um novo locale

1. Salve o novo ingles em `locales/original/`, por exemplo:

```text
locales/original/en_2026-06-01.xlf
```

2. Compare com a versao anterior:

```bash
python scripts/compare_locales.py locales/original/en.xlf locales/original/en_2026-06-01.xlf --json cache/diff_2026-06-01.json
```

3. Se o novo arquivo for a nova base, substitua ou copie para `locales/original/en.xlf`.

4. Rode novamente:

```bash
python scripts/prepare_v1_locale.py
```

5. Traduza apenas pendencias:

```bash
python scripts/ai_translate_locale.py --provider openai --model gpt-5.4-mini --mode pending --retries 2 --output output/pt_BR.ai.incremental.xlf
```

6. Valide e importe no WHM:

```bash
python scripts/validate_xlf.py output/pt_BR.ai.incremental.xlf
```

## Glossario

- `glossary/pt_BR_terms.json`: termos tecnicos e traducoes preferenciais.
- `glossary/pt_BR_phrases.json`: frases fixas que devem ser traduzidas de forma deterministica antes da IA.

Exemplo importante:

```json
"(At one quarter past the hour.)": "(Aos 15 minutos de cada hora.)"
```

## Observacoes

- `cache/` e `output/` sao ignorados pelo Git por padrao.
- `output/pt_BR.xlf` e `output/pt_BR_extended.xlf` sao artefatos reproduziveis.
- Nao edite o `source` manualmente; revise sempre o `target`.
- Preserve placeholders como `[_1]`, `%s`, `%d`, `{name}`, `{{name}}` e `:name`.
