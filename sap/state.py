from __future__ import annotations

import time

from playwright.sync_api import Page

from app_config import SAP_STATE_TIMEOUT_MS
from sap.errors import SapStateError


FAST_STATE_TIMEOUT_MS = SAP_STATE_TIMEOUT_MS


def identificar_estado(page: Page) -> str:
    """
    Uma única chamada JS.
    Não usa accessibility tree nem inner_text com auto-wait.
    """
    try:
        return str(
            page.evaluate(
                """
                () => {
                    const t = document.body?.innerText || '';

                    if (
                        t.includes('Complete delivery') &&
                        t.includes('Open Quantity') &&
                        t.includes('Req.deliv.date')
                    ) {
                        return 'AVAILABILITY_CONTROL';
                    }

                    if (
                        t.includes('Partn.Funct.') &&
                        t.includes('Display Range') &&
                        t.includes('Partners')
                    ) {
                        return 'HEADER_PARTNERS';
                    }

                    if (
                        t.includes('Header Data') &&
                        t.includes('Order Type') &&
                        t.includes('Partners')
                    ) {
                        return 'HEADER_DATA';
                    }

                    if (
                        t.includes('All items') &&
                        t.includes('Item detail') &&
                        t.includes('Purch. Order No.')
                    ) {
                        return 'OVERVIEW';
                    }

                    if (
                        t.includes('Create Sales Order: Initial Screen') &&
                        t.includes('Sales Organization') &&
                        t.includes('Distribution Channel') &&
                        t.includes('Division')
                    ) {
                        return 'VA01_INICIAL';
                    }

                    const trx =
                        document.querySelector(
                            'input[aria-label="Enter transaction code"]'
                        ) ||
                        document.getElementById('ToolbarOkCode');

                    if (trx) {
                        return 'TELA_PRINCIPAL';
                    }

                    return 'UNKNOWN';
                }
                """
            )
        )
    except Exception:
        return "UNKNOWN"


def aguardar_estado(
    page: Page,
    estados: set[str] | list[str] | tuple[str, ...],
    *,
    heartbeat=None,
    timeout_ms: int = FAST_STATE_TIMEOUT_MS,
) -> str:
    esperados = set(estados)
    inicio = time.monotonic()
    ultimo = "UNKNOWN"

    while (
        (time.monotonic() - inicio) * 1000
        <= timeout_ms
    ):
        if heartbeat is not None:
            heartbeat.assert_owned()

        ultimo = identificar_estado(page)

        if ultimo in esperados:
            return ultimo

        time.sleep(0.04)

    raise SapStateError(
        "Estado SAP inesperado. "
        f"Esperado={sorted(esperados)}; atual={ultimo}."
    )
