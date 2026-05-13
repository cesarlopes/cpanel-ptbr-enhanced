# cpanel-ptbr-enhanced

Fluxo versionado e incremental para melhorar a tradução pt_BR do cPanel & WHM a partir do locale original em inglês exportado pelo WHM em formato XLF/XLIFF 1.2.

Este projeto não é oficial da cPanel, LLC. Ele é um fluxo customizado para equipes que querem revisar, padronizar e evoluir traduções pt_BR com controle de versão.

## Objetivos

- Manter o locale original em inglês versionado.
- Manter a tradução pt_BR customizada versionada.
- Comparar novas versões do locale original com versões anteriores.
- Detectar strings novas, removidas ou alteradas.
- Traduzir apenas o que mudou.
- Preservar traduções revisadas manualmente.
- Validar placeholders, tags XML/XLF, entidades HTML e estrutura XLF.
- Gerar um arquivo final pronto para importação no WHM.

## Requisitos

- Python 3.11 ou superior.
- Arquivos `.xlf` ou `.xliff` exportados do WHM.
- Execução inicial em modo offline, sem dependência de APIs externas.

As implementações atuais usam apenas a biblioteca padrão do Python.

## Estrutura

```text
cpanel-ptbr-enhanced/
├── locales/
│   ├── original/      # Locales originais em inglês exportados do WHM
│   ├── translated/    # Traduções base ou versões traduzidas anteriores
│   └── custom/        # Traduções pt_BR customizadas e revisadas
├── glossary/          # Glossário e padronização de termos
├── scripts/           # Ferramentas de comparação, tradução, validação e build
├── cache/             # Arquivos intermediários gerados localmente
├── output/            # Arquivos finais prontos para importação no WHM
└── docs/              # Documentação de fluxo
```

## Fluxo básico

1. Exporte o locale original em inglês pelo WHM.
2. Salve o arquivo em `locales/original/`.
3. Compare a versão nova com a versão anterior:

```bash
python scripts/compare_locales.py locales/original/old.xlf locales/original/new.xlf --json cache/diff.json
```

4. Gere uma tradução incremental usando o stub offline:

```bash
python scripts/translate_incremental.py locales/original/new.xlf locales/custom/pt_BR.xlf --pending-json cache/diff.json --output locales/custom/pt_BR.updated.xlf
```

5. Valide placeholders e estrutura:

```bash
python scripts/validate_xlf.py locales/custom/pt_BR.updated.xlf
```

6. Gere o arquivo final:

```bash
python scripts/build_locale.py locales/custom/pt_BR.updated.xlf --output output/pt_BR.xlf
```

7. Importe o arquivo gerado no WHM.
8. Recompile/reconstrua a base de locales pelo WHM conforme o procedimento do ambiente.

## Glossário

O arquivo `glossary/pt_BR_terms.json` centraliza termos técnicos que devem ser preservados ou traduzidos de forma padronizada.

Termos como `cPanel`, `WHM`, `DNS`, `SPF`, `DKIM`, `DMARC`, `Exim`, `Dovecot`, `Apache`, `PHP`, `MySQL`, `SSL`, `TLS`, `SSH`, `FTP` e `API` são preservados por padrão quando não fizer sentido traduzi-los.

## Status

Base inicial funcional e evolutiva:

- Comparação offline de XLF por `trans-unit id`.
- Validação básica de XML, placeholders e tags inline.
- Tradução incremental com camada stub para futura integração com OpenAI, Claude, Gemini ou DeepL.
- Build simples para gerar arquivo final em `output/`.
