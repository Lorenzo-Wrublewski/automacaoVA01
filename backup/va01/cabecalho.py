from __future__ import annotations

import time

from playwright.sync_api import Page

from app_config import SAP_STATE_TIMEOUT_MS, carregar_json
from sap.fields import preencher, valor
from sap.loading import aguardar_sap_pronto
from sap.messages import classificar
from sap.state import aguardar_estado
from sap.status_bar import ler_status


_SELECTORS = carregar_json("selectors.json")


def _popup_snapshot(page: Page) -> dict | None:
    """
    Detecta popup SAP pelo botão X REAL e VISÍVEL.

    IMPORTANTE:
    O prefixo muda conforme o programa SAP que abriu o popup.

    Exemplos já confirmados:
        SAPMSSY0120_1-close
        SAPLSPO2300_1-close

    Portanto NÃO existe mais filtro hardcoded "SAPMSSY".
    A regra genérica é exatamente a mesma do JSON antigo do Luigi:
        [id$='-close']
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
                ]
                .filter(visible)
                .map(el => {
                    const m = el.id.match(
                        /_(\\d+)-close$/
                    );

                    return {
                        el,
                        id: el.id,
                        index: m ? Number(m[1]) : 1
                    };
                })
                .filter(x => x.index > 0)
                .sort((a, b) => b.index - a.index);

                if (!closes.length) {
                    return null;
                }

                const atual = closes[0];

                // O próprio botão X pertence à janela popup.
                const root =
                    atual.el.closest('[ct="PW"]') ||
                    atual.el.closest('.lsPopupWindow') ||
                    atual.el.closest('.urPWOuterBorder') ||
                    atual.el.parentElement?.parentElement?.parentElement ||
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

                    // Fallback: primeira linha costuma ser o título.
                    if (!title && text) {
                        title = text
                            .split(/\\r?\\n/)
                            .map(x => x.trim())
                            .find(Boolean) || '';
                    }
                }

                return {
                    close_id: atual.id,
                    window_index: atual.index,
                    title,
                    text
                };
            }
            """
        )
    except Exception:
        return None


def _clicar_x_popup(
    page: Page,
    snap: dict,
) -> None:
    """
    Clica diretamente no ID REAL encontrado no DOM.
    Funciona independentemente do prefixo SAPMSSY/SAPLSPO/etc.
    """
    close_id = str(
        snap.get("close_id") or ""
    )

    if not close_id:
        raise RuntimeError(
            "Popup SAP detectado sem ID do botão X."
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

    if clicou:
        return

    # Se sumiu entre detectar e clicar, não é erro:
    # o SAP já fechou/trocou a janela.
    if _popup_snapshot(page) is None:
        return

    raise RuntimeError(
        "Popup SAP continuou aberto, mas não foi possível "
        f"clicar no X {close_id!r}."
    )


def preencher_cabecalho(
    page: Page,
    runtime,
    solicitacao: dict,
) -> None:
    """
    Fluxo autônomo do cabeçalho:

      preenche campos
      -> Enter UMA VEZ
      -> enquanto Ship-To estiver vazio:
           popup real visível? fecha pelo X real encontrado
           loading? aguarda
           outro popup? fecha também
           status exige reenter? Enter
           caso contrário, só observa
      -> Ship-To preenchido = cabeçalho concluído

    Nunca usa "Ship-To vazio" como motivo para pressionar Enter.
    """
    runtime.etapa("CABECALHO")

    campos = _SELECTORS["overview_header"]

    preencher(
        page,
        campos["sold_to_party"],
        solicitacao.get("cliente"),
        heartbeat=runtime.heartbeat,
    )

    preencher(
        page,
        campos["purchase_order"],
        solicitacao.get("pedido_cliente"),
        heartbeat=runtime.heartbeat,
    )

    po_date = preencher(
        page,
        campos["po_date"],
        solicitacao.get("data_pedido"),
        heartbeat=runtime.heartbeat,
    )

    # Dispara o processamento UMA única vez.
    po_date.press(
        "Enter",
        timeout=1500,
    )

    aguardar_sap_pronto(
        page,
        heartbeat=runtime.heartbeat,
        appearance_grace_ms=1500,
        stable_ms=60,
    )

    # Timeout é apenas limite de segurança.
    # O fluxo segue imediatamente quando o estado real aparece.
    limite = (
        time.monotonic()
        + max(
            30.0,
            SAP_STATE_TIMEOUT_MS / 1000,
        )
    )

    status_confirmados: set[str] = set()
    ultimo_status = ""

    while time.monotonic() < limite:
        runtime.heartbeat.assert_owned()

        # PRIORIDADE 1: qualquer popup SAP realmente aberto.
        snap = _popup_snapshot(page)

        if snap is not None:
            runtime.logger(
                "[SAP] Popup detectado"
                f" | titulo={snap.get('title')!r}"
                f" | x={snap.get('close_id')!r}"
            )

            _clicar_x_popup(
                page,
                snap,
            )

            aguardar_sap_pronto(
                page,
                heartbeat=runtime.heartbeat,
                appearance_grace_ms=1200,
                stable_ms=60,
            )

            # Não toca em PO Date.
            # Volta ao topo para detectar imediatamente
            # o próximo popup, mesmo que use outro prefixo.
            time.sleep(0.05)
            continue

        # PRIORIDADE 2: verificar efeito final do cabeçalho.
        ship_to = valor(
            page,
            campos["ship_to_party"],
        )

        if ship_to:
            runtime.logger(
                f"[CABECALHO] Ship-To confirmado: {ship_to}"
            )

            aguardar_estado(
                page,
                {"OVERVIEW"},
                heartbeat=runtime.heartbeat,
            )

            return

        # PRIORIDADE 3: mensagem real do SAP.
        status = ler_status(page)

        if (
            status is not None
            and status.texto
        ):
            texto = status.texto.strip()
            ultimo_status = texto

            if (
                texto
                and texto not in status_confirmados
            ):
                decisao = classificar(
                    texto,
                    contexto="cabecalho",
                    tipo_status=status.tipo,
                )

                runtime._aplicar_decisao(
                    decisao
                )

                if decisao.acao == "reenter":
                    status_confirmados.add(
                        texto
                    )

                    runtime.logger(
                        "[SAP] Status exige confirmação explícita: Enter"
                    )

                    page.keyboard.press(
                        "Enter"
                    )

                    aguardar_sap_pronto(
                        page,
                        heartbeat=runtime.heartbeat,
                        appearance_grace_ms=1200,
                        stable_ms=60,
                    )

                    continue

        # Nenhuma ação enquanto SAP ainda está processando/apresentando
        # o próximo estado. Apenas polling.
        time.sleep(0.05)

    raise RuntimeError(
        "Cabeçalho não confirmou Ship-To Party "
        "dentro do timeout. "
        f"Último status={ultimo_status!r}."
    )
