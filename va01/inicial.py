from __future__ import annotations

import time

from playwright.sync_api import Page

from app_config import SAP_URL, carregar_json
from sap.fields import preencher
from sap.loading import aguardar_sap_pronto
from sap.state import aguardar_estado


_SELECTORS = carregar_json("selectors.json")


def _popup_inicial_snapshot(page: Page) -> dict | None:
    """
    Detecta qualquer popup SAP real pelo botão X visível.

    Não depende do título nem do programa que gerou o popup.
    Exemplos possíveis:
      SAPMSSY...
      SAPLSPO...
      outros prefixos SAP

    Regra genérica:
      qualquer botão/elemento visível cujo id termina em "-close".
    """
    try:
        return page.evaluate(
            """
            () => {
                const visible = el => {
                    if (!el) return false;

                    const s = getComputedStyle(el);
                    const r = el.getBoundingClientRect();

                    return (
                        s.display !== 'none' &&
                        s.visibility !== 'hidden' &&
                        Number(s.opacity || 1) !== 0 &&
                        r.width > 0 &&
                        r.height > 0
                    );
                };

                const closes = [
                    ...document.querySelectorAll(
                        'button[id$="-close"], [id$="-close"]'
                    )
                ].filter(visible);

                if (!closes.length) {
                    return null;
                }

                const el = closes[0];

                const root =
                    el.closest('[ct="PW"]') ||
                    el.closest('.lsPopupWindow') ||
                    el.closest('.urPWOuterBorder') ||
                    el.parentElement?.parentElement?.parentElement ||
                    null;

                let title = '';
                let text = '';

                if (root) {
                    text = (
                        root.innerText ||
                        root.textContent ||
                        ''
                    ).trim();

                    const titleEl =
                        root.querySelector('[id$="-ttltxt"]') ||
                        root.querySelector('.urPWTitle') ||
                        root.querySelector('[role="heading"]');

                    if (titleEl) {
                        title = (
                            titleEl.innerText ||
                            titleEl.textContent ||
                            ''
                        ).trim();
                    }

                    if (!title && text) {
                        title = text
                            .split(/\\r?\\n/)
                            .map(x => x.trim())
                            .find(Boolean) || '';
                    }
                }

                return {
                    close_id: el.id,
                    title,
                    text
                };
            }
            """
        )
    except Exception:
        return None


def _fechar_popups_iniciais(
    page: Page,
    runtime,
    *,
    espera_inicial_ms: int = 2500,
    max_popups: int = 10,
) -> None:
    """
    Ao abrir o SAP pela primeira vez no dia pode aparecer
    "System Messages" antes de qualquer transação.

    O bot observa por uma pequena janela de chegada e fecha
    autonomamente qualquer popup real pelo X.

    Depois de fechar um popup, volta a observar porque o SAP
    pode abrir outro em sequência.
    """
    limite_inicial = (
        time.monotonic()
        + (espera_inicial_ms / 1000)
    )

    snap = None

    while time.monotonic() < limite_inicial:
        runtime.heartbeat.assert_owned()

        snap = _popup_inicial_snapshot(page)

        if snap is not None:
            break

        # Se a tela principal já apareceu sem popup,
        # ainda aguardamos um pouco porque System Messages
        # pode entrar logo depois do carregamento inicial.
        time.sleep(0.05)

    if snap is None:
        return

    for _ in range(max_popups):
        if snap is None:
            return

        close_id = str(
            snap.get("close_id") or ""
        )

        runtime.logger(
            "[SAP] Popup inicial detectado"
            + (
                f": {snap.get('title')}"
                if snap.get("title")
                else ""
            )
            + f" | x={close_id!r}"
        )

        clicou = page.evaluate(
            """
            id => {
                const el = document.getElementById(id);

                if (!el) return false;

                const s = getComputedStyle(el);
                const r = el.getBoundingClientRect();

                if (
                    s.display === 'none' ||
                    s.visibility === 'hidden' ||
                    Number(s.opacity || 1) === 0 ||
                    r.width <= 0 ||
                    r.height <= 0
                ) {
                    return false;
                }

                el.click();
                return true;
            }
            """,
            close_id,
        )

        if not clicou:
            # Revalida: se desapareceu entre detectar e clicar,
            # considera fechado e segue.
            if _popup_inicial_snapshot(page) is None:
                return

            raise RuntimeError(
                "Popup inicial SAP permaneceu aberto, "
                f"mas não foi possível clicar no X {close_id!r}."
            )

        aguardar_sap_pronto(
            page,
            heartbeat=runtime.heartbeat,
            appearance_grace_ms=1000,
            stable_ms=60,
        )

        # Dá chance de um segundo popup surgir após o primeiro.
        fim_quiet = time.monotonic() + 1.5
        snap = None

        while time.monotonic() < fim_quiet:
            runtime.heartbeat.assert_owned()

            snap = _popup_inicial_snapshot(page)

            if snap is not None:
                break

            time.sleep(0.05)

        if snap is None:
            return

    raise RuntimeError(
        "Mais de 10 popups apareceram na abertura do SAP."
    )


def abrir_va01(
    page: Page,
    runtime,
) -> None:
    runtime.etapa("ABRINDO_SAP")

    page.goto(
        SAP_URL,
        wait_until="domcontentloaded",
    )

    # Login/SSO pode demorar. O estado retorna assim
    # que o campo de transação existir.
    aguardar_estado(
        page,
        {"TELA_PRINCIPAL"},
        heartbeat=runtime.heartbeat,
        timeout_ms=60_000,
    )

    # Primeira abertura do SAP no dia pode trazer
    # "System Messages" por cima da tela principal.
    # Fecha qualquer popup inicial real antes de preencher VA01.
    _fechar_popups_iniciais(
        page,
        runtime,
    )

    # Confirma que continuamos na tela principal após fechar
    # os popups iniciais.
    aguardar_estado(
        page,
        {"TELA_PRINCIPAL"},
        heartbeat=runtime.heartbeat,
        timeout_ms=60_000,
    )

    runtime.etapa("ABRINDO_VA01")

    campo = preencher(
        page,
        _SELECTORS["transaction"]["field"],
        "VA01",
        heartbeat=runtime.heartbeat,
    )

    campo.press(
        "Enter",
        timeout=1500,
    )

    # Dá chance real do loader aparecer.
    aguardar_sap_pronto(
        page,
        heartbeat=runtime.heartbeat,
        appearance_grace_ms=1500,
        stable_ms=60,
    )

    aguardar_estado(
        page,
        {"VA01_INICIAL"},
        heartbeat=runtime.heartbeat,
    )


def preencher_dados_iniciais(
    page: Page,
    runtime,
    solicitacao: dict,
) -> None:
    runtime.etapa("VA01_INICIAL")

    inicial = _SELECTORS["va01_initial"]

    preencher(
        page,
        inicial["order_type"],
        solicitacao["tipo_ordem"],
        heartbeat=runtime.heartbeat,
    )

    preencher(
        page,
        inicial["sales_org"],
        solicitacao["organizacao_vendas"],
        heartbeat=runtime.heartbeat,
    )

    preencher(
        page,
        inicial["distribution_channel"],
        solicitacao["canal_distribuicao"],
        heartbeat=runtime.heartbeat,
    )

    division = preencher(
        page,
        inicial["division"],
        solicitacao["setor_atividade"],
        heartbeat=runtime.heartbeat,
    )

    division.press(
        "Enter",
        timeout=1500,
    )

    # O erro #28 acontecia aqui:
    # o código desistia do OVERVIEW em 2,5s.
    # Agora espera o loading real e depois o estado real.
    aguardar_sap_pronto(
        page,
        heartbeat=runtime.heartbeat,
        appearance_grace_ms=1500,
        stable_ms=60,
    )

    aguardar_estado(
        page,
        {"OVERVIEW"},
        heartbeat=runtime.heartbeat,
    )
