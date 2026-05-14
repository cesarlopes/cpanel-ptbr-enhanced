#!/bin/bash
set -e

REPO_RAW="https://raw.githubusercontent.com/cesarlopes/cpanel-ptbr-enhanced/main"
LOCALE_TAG="i_pt_br_enhanced"
XLF_FILE="${LOCALE_TAG}.custom.xlf"
YAML_FILE="${LOCALE_TAG}.yaml"
LOCALE_IMPORT="/usr/local/cpanel/scripts/locale_import"
I_LOCALES_DIR="/var/cpanel/i_locales"
SRC_DIR="/usr/local/src"

# Verificar root
if [ "$(id -u)" -ne 0 ]; then
    echo "ERRO: Execute este script como root." >&2
    exit 1
fi

# Verificar se é um servidor cPanel
if [ ! -x "$LOCALE_IMPORT" ]; then
    echo "ERRO: $LOCALE_IMPORT não encontrado. Este script requer um servidor cPanel." >&2
    exit 1
fi

echo "==> Instalando locale: $LOCALE_TAG"

# 1. Criar diretório de configuração de locales customizados
mkdir -p "$I_LOCALES_DIR"

# 2. Baixar arquivo de configuração (display name)
echo "==> Baixando configuração do locale..."
curl -fsSL -o "${I_LOCALES_DIR}/${YAML_FILE}" "${REPO_RAW}/${YAML_FILE}"

# 3. Baixar arquivo de traduções
echo "==> Baixando traduções..."
curl -fsSL -o "${SRC_DIR}/${XLF_FILE}" "${REPO_RAW}/${XLF_FILE}"

# 4. Importar locale
echo "==> Importando locale (isso pode levar alguns minutos)..."
"$LOCALE_IMPORT" --import="${SRC_DIR}/${XLF_FILE}"

echo ""
echo "==> Instalação concluída!"
echo "    O locale '$LOCALE_TAG' agora aparece como 'Português Brasil (completo)'"
echo "    no seletor de idioma do cPanel e WHM."
