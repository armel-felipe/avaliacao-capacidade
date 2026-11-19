#!/bin/bash
set -e

SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="$SCRIPT_DIR/.venv"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Erro: Python 3 não encontrado. Instale o Python 3 e tente novamente." >&2
    exit 1
fi

if [ ! -x "$VENV_DIR/bin/python" ]; then
    echo "Criando ambiente virtual em .venv..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

echo "Instalando/verificando dependências..."
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

echo "Iniciando o simulador..."
exec "$VENV_DIR/bin/python" -m streamlit run app_dimensionamento5.py "$@"
