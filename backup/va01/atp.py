from __future__ import annotations

from playwright.sync_api import Page

from app_config import carregar_json
from sap.state import aguardar_estado


_SELECTORS = carregar_json("selectors.json")


def tratar_availability_control(
    page: Page,
    runtime,
) -> None:
    runtime.etapa("ATP_COMPLETE_DELIVERY")

    titulo = page.get_by_text(
        "Complete delivery",
        exact=True,
    )

    visivel = None

    for i in range(titulo.count()):
        candidato = titulo.nth(i)

        if candidato.is_visible(timeout=100):
            visivel = candidato
            break

    if visivel is None:
        raise RuntimeError(
            "Seção Complete delivery não encontrada."
        )

    container = visivel.locator(
        "xpath=ancestor::*[.//*[@title='Copy']][1]"
    )

    botao = None

    if container.count():
        copies = container.get_by_title(
            "Copy",
            exact=True,
        )

        for i in range(copies.count()):
            candidato = copies.nth(i)

            if candidato.is_visible(timeout=100):
                botao = candidato
                break

    if botao is None:
        following = visivel.locator(
            "xpath=following::*[@title='Copy'][1]"
        )

        if (
            following.count()
            and following.first.is_visible(timeout=100)
        ):
            botao = following.first

    if botao is None:
        raise RuntimeError(
            "Check verde/Copy do bloco Complete delivery "
            "não encontrado."
        )

    botao.click(timeout=1500)

    runtime.estabilizar(
        contexto="atp",
        appearance_grace_ms=180,
    )

    aguardar_estado(
        page,
        {"OVERVIEW"},
        heartbeat=runtime.heartbeat,
    )
