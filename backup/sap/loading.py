from __future__ import annotations

import time

from playwright.sync_api import Page

from app_config import SAP_LOADING_TIMEOUT_MS
from sap.errors import SapLoadingTimeout


def _loader_visivel(page: Page) -> bool:
    """
    Uma única leitura JS do DOM.
    Não usa locator.count()/is_visible() repetidamente.
    """
    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const el =
                        document.getElementById('ur-loading-itm2');

                    if (!el) return false;

                    const s = getComputedStyle(el);

                    return (
                        s.display !== 'none' &&
                        s.visibility !== 'hidden' &&
                        Number(s.opacity || 1) !== 0 &&
                        el.getClientRects().length > 0
                    );
                }
                """
            )
        )
    except Exception:
        return False


def aguardar_sap_pronto(
    page: Page,
    *,
    heartbeat=None,
    timeout_ms: int = SAP_LOADING_TIMEOUT_MS,
    appearance_grace_ms: int = 0,
    stable_ms: int = 40,
) -> None:
    """
    REGRA:
    - sem loader visível -> retorna imediatamente;
    - depois de ação que pode disparar loader, observa somente
      a curta janela appearance_grace_ms;
    - se loader aparecer -> aí sim espera até desaparecer.

    Portanto esta função NÃO cria espera longa quando SAP está livre.
    """
    inicio = time.monotonic()

    if not _loader_visivel(page):
        if appearance_grace_ms <= 0:
            return

        limite_grace = (
            inicio + appearance_grace_ms / 1000
        )

        while time.monotonic() < limite_grace:
            if heartbeat is not None:
                heartbeat.assert_owned()

            if _loader_visivel(page):
                break

            time.sleep(0.02)
        else:
            return

    livre_desde = None

    while True:
        if heartbeat is not None:
            heartbeat.assert_owned()

        agora = time.monotonic()

        if (agora - inicio) * 1000 > timeout_ms:
            raise SapLoadingTimeout(
                "SAP permaneceu em loading por mais de "
                f"{timeout_ms / 1000:.0f}s."
            )

        if _loader_visivel(page):
            livre_desde = None
        else:
            if livre_desde is None:
                livre_desde = agora

            if (
                agora - livre_desde
            ) * 1000 >= stable_ms:
                return

        time.sleep(0.03)
