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
i_pt_br_enhanced.custom.xlf
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

O arquivo `i_pt_br_enhanced.custom.xlf` na raiz e uma copia pronta para
importar no WHM como locale nao padrao `i_pt_br_enhanced`. Ele vem do build
extended sem grupos plurais problematicos para o importador do cPanel.

## Instalar no cPanel/WHM

Em um servidor com cPanel & WHM, acesse via SSH como `root` e execute o instalador:

```bash
bash <(curl -sL https://raw.githubusercontent.com/cesarlopes/cpanel-ptbr-enhanced/main/install.sh)
```

O script baixa as traducoes, configura o nome de exibicao do locale e executa o import automaticamente.

<details>
<summary>Instalacao manual (passo a passo)</summary>

```bash
mkdir -p /var/cpanel/i_locales
curl -fsSL -o /var/cpanel/i_locales/i_pt_br_enhanced.yaml https://raw.githubusercontent.com/cesarlopes/cpanel-ptbr-enhanced/main/i_pt_br_enhanced.yaml
curl -fsSL -o /usr/local/src/i_pt_br_enhanced.custom.xlf https://raw.githubusercontent.com/cesarlopes/cpanel-ptbr-enhanced/main/i_pt_br_enhanced.custom.xlf
/usr/local/cpanel/scripts/locale_import --import=/usr/local/src/i_pt_br_enhanced.custom.xlf
```

</details>

### Aplicar o locale em todas as contas

Depois de instalar o locale, voce pode definir `i_pt_br_enhanced` como idioma
das contas cPanel existentes:

```bash
for user in $(ls /var/cpanel/users)
do
  echo "Atualizando $user"
  uapi --user="$user" Locale set_locale locale='i_pt_br_enhanced'
done
```

Em seguida, reconstrua os bancos de locale do cPanel:

```bash
/usr/local/cpanel/bin/build_locale_databases
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

Se a UI local ficar lenta depois de importar ou traduzir muitos registros,
atualize os indices e estatisticas do SQLite:

```bash
python scripts/optimize_locale_db.py --db cache/translations.sqlite
```

A UI usa uma tabela materializada chamada `locale_unit_status`. Os scripts
principais a atualizam automaticamente; para reconstruir manualmente:

```bash
python scripts/refresh_locale_status.py --db cache/translations.sqlite
```

Gere os arquivos finais a partir do banco:

```bash
python scripts/build_locale.py --from-db --db cache/translations.sqlite --source locales/original/en.xlf
```

Para gerar uma variante de teste como locale nao padrao no WHM:

```bash
python scripts/build_locale.py --from-db --db cache/translations.sqlite --source locales/original/en.xlf --locale-tag i_pt_br_enhanced --fallback-locale pt_BR --number-formatting pt_BR --character-orientation left-to-right
```

Isso gera `output/i_pt_br_enhanced.custom.xlf`,
`output/i_pt_br_enhanced_extended.custom.xlf` e um JSON com as configuracoes
esperadas para criar/copiar o locale no WHM. O fallback precisa ser configurado
no WHM; ele nao fica dentro do XLF. O nome que aparece no seletor do cPanel
tambem vem da configuracao do locale nao padrao. Use o valor `display_name` do
JSON gerado, por exemplo `Português Brasil (completo)`.

Se o importador do cPanel falhar em unidades de pluralizacao `x-implied`,
`x-explicit` ou em `source` vazio, gere uma variante sem grupos plurais:

```bash
python scripts/build_locale.py --from-db --db cache/translations.sqlite --source locales/original/en.xlf --locale-tag i_pt_br_enhanced --fallback-locale pt_BR --number-formatting pt_BR --character-orientation left-to-right --exclude-plurals --output output/i_pt_br_enhanced.no_plurals.custom.xlf --extended-output output/i_pt_br_enhanced_extended.no_plurals.custom.xlf
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

### Traducao direta pelo SQLite

Depois que as tabelas `locale_*` existirem, o fluxo recomendado e traduzir direto no banco. O XLF fica apenas como entrada/saida do WHM.

Canonical pendente com Grok:

```bash
python scripts/ai_translate_db.py --db cache/translations.sqlite --scope canonical --provider xai --model grok-4-1-fast-non-reasoning --fallback-model gpt-5-mini --mode pending --limit 1000 --retries 2 --checkpoint-every 250 --concurrency 3
```

Extended pendente com Grok:

```bash
python scripts/ai_translate_db.py --db cache/translations.sqlite --scope extended --provider xai --model grok-4-1-fast-non-reasoning --fallback-model gpt-5-mini --mode pending --limit 1000 --retries 2 --checkpoint-every 250 --concurrency 3
```

Use `--mode pending` para traduzir apenas o que ainda nao tem target valido. Use `--mode all` somente quando quiser refazer tambem itens que ja possuem target da cPanel ou IA.

Para revisar apenas unidades cuja melhor traducao atual veio do cPanel:

```bash
python scripts/ai_translate_db.py --db cache/translations.sqlite --scope extended --provider xai --model grok-4-1-fast-non-reasoning --mode review-origin --review-origin cpanel --limit 1000 --retries 2 --checkpoint-every 250 --concurrency 3
```

Esse modo nao apaga a traducao cPanel; ele adiciona uma alternativa `ai_cache`, que tem prioridade no build final.

### Versionar revisoes manuais

O SQLite completo fica local, mas as revisoes manuais podem ser exportadas para um JSONL pequeno e versionavel:

```bash
python scripts/export_reviewed_targets.py --db cache/translations.sqlite --output data/manual_targets.jsonl
```

Para restaurar essas revisoes em outro banco depois de importar os XLFs:

```bash
python scripts/import_reviewed_targets.py --db cache/translations.sqlite --input data/manual_targets.jsonl
```

Para backup completo dos targets atuais, incluindo cPanel, IA e manuais, use um snapshot compactado:

```bash
python scripts/export_locale_targets.py --db cache/translations.sqlite --origin all --output data/locale_targets_snapshot.jsonl.gz
```

Para restaurar:

```bash
python scripts/import_locale_targets.py --db cache/translations.sqlite --input data/locale_targets_snapshot.jsonl.gz
```

Snapshots completos podem ficar grandes e ficam ignorados pelo Git. Para versionamento normal, prefira `data/manual_targets.jsonl`.

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
