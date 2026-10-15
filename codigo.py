from __future__ import annotations

import argparse
import sys
import time
import traceback

from playwright.sync_api import sync_playwright

from app_config import SAP_HEADLESS
from database.heartbeat import HeartbeatWorker
from database.repository import (
    carregar_solicitacao,
    finalizar_erro,
    finalizar_sucesso,
)
from sap.runtime import SapRuntime
from va01.cabecalho import preencher_cabecalho
from va01.inicial import abrir_va01, preencher_dados_iniciais
from va01.itens import processar_itens
from va01.parceiros import processar_parceiros
from va01.salvamento import salvar_ov


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--solicitacao-id", type=int, required=True)
    parser.add_argument("--executor-id", type=int, required=True)
    parser.add_argument("--lock-token", required=True)
    return parser.parse_args()


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> int:
    args = parse_args()
    inicio_total = time.monotonic()

    hb = HeartbeatWorker(
        args.solicitacao_id,
        args.executor_id,
        args.lock_token,
    )
    hb.start()

    etapa = "INICIALIZANDO"

    try:
        solicitacao = carregar_solicitacao(
            args.solicitacao_id,
            args.executor_id,
            args.lock_token,
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=SAP_HEADLESS,
            )
            context = browser.new_context()
            page = context.new_page()

            # Evita QUALQUER ação Playwright esquecida ficar 30s parada.
            # Loading e navegação possuem waits próprios.
            page.set_default_timeout(3000)
            page.set_default_navigation_timeout(60000)

            runtime = SapRuntime(
                page,
                hb,
                solicitacao_id=args.solicitacao_id,
                executor_id=args.executor_id,
                lock_token=args.lock_token,
                logger=log,
            )

            try:
                etapa = "ABRINDO_VA01"
                abrir_va01(page, runtime)

                etapa = "VA01_INICIAL"
                preencher_dados_iniciais(
                    page, runtime, solicitacao
                )

                etapa = "CABECALHO"
                preencher_cabecalho(
                    page, runtime, solicitacao
                )

                etapa = "PARCEIROS"
                processar_parceiros(
                    page, runtime, solicitacao
                )

                etapa = "ITENS"
                processar_itens(
                    page, runtime, solicitacao
                )

                etapa = "SALVANDO"
                ov, mensagem = salvar_ov(
                    page, runtime
                )

                runtime.etapa("FINALIZADO")

                finalizar_sucesso(
                    args.solicitacao_id,
                    args.executor_id,
                    args.lock_token,
                    ov,
                    mensagem,
                )

                log(
                    f"[PERF] TOTAL: "
                    f"{time.monotonic() - inicio_total:.2f}s"
                )
                log(f"[SUCCESS] OV criada: {ov}")
                return 0

            finally:
                context.close()
                browser.close()

    except Exception as exc:
        erro = (
            f"{type(exc).__name__}: {exc}\n"
            f"{traceback.format_exc()}"
        )
        log(f"[ERROR] {erro}")

        try:
            finalizar_erro(
                args.solicitacao_id,
                args.executor_id,
                args.lock_token,
                erro,
                etapa,
            )
        except Exception as db_exc:
            log(
                "[ERROR] Falha adicional ao registrar "
                f"erro no SQL: {db_exc}"
            )

        return 1

    finally:
        hb.stop()


if __name__ == "__main__":
    sys.exit(main())
