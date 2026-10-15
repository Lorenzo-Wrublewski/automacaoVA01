from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
CONFIG_DIR = BASE_DIR / "config"


def carregar_env_simples(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return

    for linha in path.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()

        if not linha or linha.startswith("#") or "=" not in linha:
            continue

        chave, valor = linha.split("=", 1)
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")

        if chave and chave not in os.environ:
            os.environ[chave] = valor


def env_obrigatoria(nome: str) -> str:
    valor = os.environ.get(nome, "").strip()

    if not valor:
        raise RuntimeError(
            f"Variável obrigatória {nome} não configurada no .env."
        )

    return valor


def env_bool(nome: str, default: bool = False) -> bool:
    valor = os.environ.get(nome)

    if valor is None:
        return default

    return valor.strip().lower() in {
        "1", "true", "yes", "sim", "on",
    }


def env_int(nome: str, default: int) -> int:
    valor = os.environ.get(nome)

    if valor is None or not valor.strip():
        return default

    return int(valor)


def carregar_json(nome: str) -> dict:
    caminho = CONFIG_DIR / nome
    return json.loads(caminho.read_text(encoding="utf-8"))


carregar_env_simples()

DB_SERVER = env_obrigatoria("VA01_DB_SERVER")
DB_DATABASE = env_obrigatoria("VA01_DB_DATABASE")
DB_USER = env_obrigatoria("VA01_DB_USER")
DB_PASSWORD = env_obrigatoria("VA01_DB_PASSWORD")

SAP_URL = env_obrigatoria("VA01_SAP_URL")
SAP_HEADLESS = env_bool("VA01_SAP_HEADLESS", False)

SAP_ACTION_TIMEOUT_MS = env_int(
    "VA01_SAP_ACTION_TIMEOUT_MS",
    15_000,
)
SAP_LOADING_TIMEOUT_MS = env_int(
    "VA01_SAP_LOADING_TIMEOUT_MS",
    180_000,
)
SAP_STATE_TIMEOUT_MS = env_int(
    "VA01_SAP_STATE_TIMEOUT_MS",
    20_000,
)
SAP_STATUS_TIMEOUT_MS = env_int(
    "VA01_SAP_STATUS_TIMEOUT_MS",
    15_000,
)
HEARTBEAT_INTERVAL_SECONDS = env_int(
    "VA01_HEARTBEAT_INTERVAL_SECONDS",
    5,
)
ORCHESTRATOR_STALE_HEARTBEAT_SECONDS = env_int(
    "VA01_STALE_HEARTBEAT_SECONDS",
    90,
)
