# cPanel pt_BR Enhanced UI

Interface local em PHP para revisar e editar traducoes armazenadas no SQLite.

## Rodar localmente

Na raiz do projeto:

```powershell
php -S localhost:8080 -t ui/public
```

Abra:

```text
http://localhost:8080
```

## Banco esperado

Por padrao a UI usa:

```text
cache/translations.sqlite
```

Antes de abrir a UI, gere ou atualize as tabelas de revisao:

```powershell
python scripts/import_locale_to_db.py --db cache/translations.sqlite --original locales/original/en.xlf --translated locales/translated/pt_br.xlf locales/translated/pt_br_2.xlf
```

## Recursos da v1

- Lista paginada de unidades.
- Busca por ID, source, target atual e hash.
- Filtros por pendentes, IA, cPanel, manual e revisadas.
- Edicao manual de target.
- Salvamento como `origin=manual`, `provider=human` e `is_reviewed=1`.

