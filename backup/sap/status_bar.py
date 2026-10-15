from __future__ import annotations

import time
from dataclasses import dataclass

from playwright.sync_api import Page

from app_config import SAP_STATUS_TIMEOUT_MS


@dataclass
class StatusSap:
    texto: str
    tipo: str


def ler_status(page: Page) -> StatusSap | None:
    """
    Lê texto + metadados do rodapé em UMA chamada JS.
    """
    try:
        dados = page.evaluate(
            """
            () => {
                const txt = document.getElementById(
                    'wnd[0]/sbar_msg-txt'
                );
                const img = document.getElementById(
                    'wnd[0]/sbar_img'
                );

                const texto = (
                    txt?.innerText ||
                    txt?.textContent ||
                    ''
                ).trim();

                if (!texto) return null;

                const meta = [
                    img?.getAttribute('title') || '',
                    img?.getAttribute('alt') || '',
                    img?.getAttribute('class') || '',
                    img?.getAttribute('src') || ''
                ].join(' ').toLowerCase();

                let tipo = 'unknown';

                if (
                    meta.includes('error') ||
                    meta.includes('negative')
                ) {
                    tipo = 'error';
                } else if (
                    meta.includes('warning') ||
                    meta.includes('critical')
                ) {
                    tipo = 'warning';
                } else if (
                    meta.includes('success') ||
                    meta.includes('positive')
                ) {
                    tipo = 'success';
                } else if (
                    meta.includes('information') ||
                    meta.includes('info') ||
                    meta.includes('neutral')
                ) {
                    tipo = 'info';
                }

                return {texto, tipo};
            }
            """
        )
    except Exception:
        return None

    if not dados:
        return None

    return StatusSap(
        texto=str(dados["texto"]),
        tipo=str(dados["tipo"]),
    )


def aguardar_novo_status(
    page: Page,
    anterior: str,
    *,
    heartbeat=None,
    timeout_ms: int = SAP_STATUS_TIMEOUT_MS,
) -> StatusSap | None:
    inicio = time.monotonic()

    while (
        time.monotonic() - inicio
    ) * 1000 <= timeout_ms:
        if heartbeat is not None:
            heartbeat.assert_owned()

        atual = ler_status(page)

        if (
            atual is not None
            and atual.texto
            and atual.texto != anterior
        ):
            return atual

        time.sleep(0.05)

    return ler_status(page)
