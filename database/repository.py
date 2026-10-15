from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from database.connection import get_conn


def _row_to_dict(cursor, row) -> dict:
    colunas = [c[0] for c in cursor.description]
    return dict(zip(colunas, row))


def _json_or_none(valor):
    if valor is None:
        return None

    if isinstance(valor, (dict, list)):
        return valor

    texto = str(valor).strip()

    if not texto:
        return None

    return json.loads(texto)


def carregar_solicitacao(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
) -> dict:
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT *
            FROM dbo.VA01_Solicitacao
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status = 'RUNNING'
            """,
            solicitacao_id,
            executor_id,
            lock_token,
        )

        row = cur.fetchone()

        if row is None:
            raise RuntimeError(
                "Solicitação não encontrada ou posse SQL inválida."
            )

        solicitacao = _row_to_dict(cur, row)

        cur.execute(
            """
            SELECT *
            FROM dbo.VA01_Item
            WHERE solicitacao_id = ?
            ORDER BY sequencia, item_id
            """,
            solicitacao_id,
        )

        itens = [
            _row_to_dict(cur, item)
            for item in cur.fetchall()
        ]

    if "parametros_json" in solicitacao:
        solicitacao["parametros_json"] = _json_or_none(
            solicitacao.get("parametros_json")
        )

    for item in itens:
        if "parametros_json" in item:
            item["parametros_json"] = _json_or_none(
                item.get("parametros_json")
            )

    solicitacao["itens"] = itens
    return solicitacao


def heartbeat(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
    etapa: str | None,
) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE dbo.VA01_Solicitacao
            SET
                heartbeat_em = SYSDATETIME(),
                ultima_etapa = COALESCE(?, ultima_etapa),
                atualizado_em = SYSDATETIME()
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status = 'RUNNING'
            """,
            etapa,
            solicitacao_id,
            executor_id,
            lock_token,
        )

        alteradas = cur.rowcount
        conn.commit()

    return alteradas == 1


def registrar_mensagem(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
    mensagem: str,
) -> None:
    mensagem = (mensagem or "").strip()

    if not mensagem:
        return

    with get_conn() as conn:
        conn.cursor().execute(
            """
            UPDATE dbo.VA01_Solicitacao
            SET
                ultima_mensagem_sap = ?,
                atualizado_em = SYSDATETIME()
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status = 'RUNNING'
            """,
            mensagem[:2000],
            solicitacao_id,
            executor_id,
            lock_token,
        )
        conn.commit()


def finalizar_sucesso(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
    sap_ov: str,
    mensagem: str | None = None,
) -> None:
    sap_ov = str(sap_ov).strip()

    if not sap_ov:
        raise ValueError(
            "SUCCESS exige número de OV."
        )

    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE dbo.VA01_Solicitacao
            SET
                status = 'SUCCESS',
                sap_ov = ?,
                ultima_etapa = 'CONCLUIDO',
                ultima_mensagem_sap = COALESCE(?, ultima_mensagem_sap),
                ultimo_erro = NULL,
                heartbeat_em = SYSDATETIME(),
                finalizado_em = SYSDATETIME(),
                atualizado_em = SYSDATETIME()
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status = 'RUNNING'
            """,
            sap_ov,
            mensagem,
            solicitacao_id,
            executor_id,
            lock_token,
        )

        if cur.rowcount != 1:
            raise RuntimeError(
                "Não foi possível finalizar SUCCESS. "
                "A posse SQL pode ter sido perdida."
            )

        conn.commit()


def finalizar_erro(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
    erro: str,
    etapa: str | None = None,
) -> None:
    with get_conn() as conn:
        conn.cursor().execute(
            """
            UPDATE dbo.VA01_Solicitacao
            SET
                status = 'ERROR',
                ultima_etapa = COALESCE(?, ultima_etapa),
                ultimo_erro = ?,
                heartbeat_em = SYSDATETIME(),
                finalizado_em = SYSDATETIME(),
                atualizado_em = SYSDATETIME()
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status IN ('CLAIMED', 'RUNNING')
            """,
            etapa,
            str(erro),
            solicitacao_id,
            executor_id,
            lock_token,
        )
        conn.commit()


def obter_execucao(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            SELECT
                status,
                sap_ov,
                ultima_etapa,
                ultima_mensagem_sap,
                ultimo_erro,
                DATEDIFF(
                    SECOND,
                    heartbeat_em,
                    SYSDATETIME()
                ) AS heartbeat_age_seconds
            FROM dbo.VA01_Solicitacao
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
            """,
            solicitacao_id,
            executor_id,
            lock_token,
        )

        row = cur.fetchone()

        if row is None:
            return None

        return _row_to_dict(cur, row)
