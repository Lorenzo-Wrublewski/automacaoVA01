from __future__ import annotations

import pyodbc

from app_config import (
    DB_DATABASE,
    DB_PASSWORD,
    DB_SERVER,
    DB_USER,
)


def _driver() -> str:
    drivers = set(pyodbc.drivers())

    if "ODBC Driver 18 for SQL Server" in drivers:
        return "ODBC Driver 18 for SQL Server"

    if "ODBC Driver 17 for SQL Server" in drivers:
        return "ODBC Driver 17 for SQL Server"

    raise RuntimeError(
        "ODBC Driver 17/18 for SQL Server não encontrado."
    )


def get_conn():
    driver = _driver()

    conn_str = (
        f"DRIVER={{{driver}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_DATABASE};"
        f"UID={DB_USER};"
        f"PWD={DB_PASSWORD};"
        "TrustServerCertificate=yes;"
    )

    return pyodbc.connect(conn_str, timeout=10)
