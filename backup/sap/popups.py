from __future__ import annotations

import time
from dataclasses import dataclass

from playwright.sync_api import Page

from sap.loading import aguardar_sap_pronto


@dataclass
class PopupSap:
    texto: str
    titulo: str


def _popup_visivel(page: Page):
    """
    SAP WebGUI reutiliza wnd[1] para os popups sequenciais.

    Só considera popup real quando wnd[1] está VISÍVEL.
    Texto velho escondido no DOM não conta.
    """
    wnd = page.locator('[id="wnd[1]"]')

    try:
        if wnd.count() == 0:
            return None

        if not wnd.first.is_visible(timeout=100):
            return None

        return wnd.first
    except Exception:
        return None


def _ler_popup(wnd) -> PopupSap:
    try:
        texto = (wnd.inner_text(timeout=300) or "").strip()
    except Exception:
        texto = ""

    titulo = ""

    for seletor in (
        '[id$="-ttltxt"]',
        '[role="heading"]',
        '.lsPopupWindow__title',
        '.lsPopupWindow--title',
    ):
        try:
            loc = wnd.locator(seletor)

            if (
                loc.count() > 0
                and loc.first.is_visible(timeout=80)
            ):
                titulo = (
                    loc.first.inner_text(timeout=200) or ""
                ).strip()

                if titulo:
                    break
        except Exception:
            continue

    return PopupSap(
        texto=texto,
        titulo=titulo,
    )


def _clicar_x_mapeado(page: Page, wnd) -> None:
    """
    MAPEAMENTO FEITO PELO CODEGEN em 14/10/2026:

    Tanto "Partner selection" quanto "Open quotations"
    possuem o X superior como um BUTTON acessível.

    Não dependemos do glyph/font do accessible-name porque ele
    pode aparecer como caractere privado/quadrado fora do SAP.
    Dentro de wnd[1], identificamos o botão superior direito pela
    posição física — é o mesmo controle que o Codegen destacou.

    Fallback: ID SAP *-close, caso esteja disponível.
    """

    # 1) Primeiro tenta o botão superior direito por geometria,
    #    limitado SOMENTE ao popup wnd[1].
    try:
        botoes = wnd.get_by_role("button")

        info = botoes.evaluate_all(
            """
            (els) => {
                const visible = el => {
                    const s = getComputedStyle(el);
                    const r = el.getBoundingClientRect();

                    return (
                        s.display !== 'none' &&
                        s.visibility !== 'hidden' &&
                        r.width > 0 &&
                        r.height > 0
                    );
                };

                const root = els.length
                    ? els[0].closest('[id="wnd[1]"]')
                    : null;

                const rr = root
                    ? root.getBoundingClientRect()
                    : null;

                return els.map((el, index) => {
                    const r = el.getBoundingClientRect();

                    return {
                        index,
                        visible: visible(el),
                        left: r.left,
                        top: r.top,
                        right: r.right,
                        bottom: r.bottom,
                        width: r.width,
                        height: r.height,
                        rootRight: rr ? rr.right : 0,
                        rootTop: rr ? rr.top : 0,
                        rootWidth: rr ? rr.width : 0,
                        rootHeight: rr ? rr.height : 0
                    };
                });
            }
            """
        )

        candidatos = []

        for b in info:
            if not b.get("visible"):
                continue

            # X superior direito:
            # fica no quarto superior e próximo da borda direita.
            if (
                b["rootWidth"] > 0
                and b["rootHeight"] > 0
                and b["top"]
                    <= b["rootTop"] + b["rootHeight"] * 0.25
                and b["right"]
                    >= b["rootRight"] - b["rootWidth"] * 0.18
            ):
                candidatos.append(b)

        if candidatos:
            # O mais alto e mais à direita é o X superior.
            alvo = sorted(
                candidatos,
                key=lambda b: (b["top"], -b["right"]),
            )[0]

            botoes.nth(alvo["index"]).click(
                timeout=1200,
                force=True,
            )
            return
    except Exception:
        pass

    # 2) Fallback pelo padrão de ID confirmado anteriormente.
    try:
        x = page.locator(
            '[id^="SAPMSSY"][id$="-close"]:visible'
        )

        if x.count() > 0:
            x.first.click(
                timeout=1200,
                force=True,
            )
            return
    except Exception:
        pass

    # Revalida antes de considerar erro.
    # Se wnd[1] desapareceu, o popup já fechou.
    if _popup_visivel(page) is None:
        return

    raise RuntimeError(
        "Popup SAP visível, mas o botão X superior "
        "mapeado pelo Codegen não pôde ser acionado."
    )


def fechar_todos_popups(
    page: Page,
    *,
    heartbeat=None,
    contexto: str = "",
    max_popups: int = 20,
) -> list[PopupSap]:
    """
    Máquina genérica de popups:

    wnd[1] visível
      -> clica X superior mapeado
      -> espera reação/loading
      -> observa se outro wnd[1] aparece
      -> fecha também

    O SAP pode REUTILIZAR wnd[1] no segundo popup.
    Por isso nunca associamos wnd[1] a "um único popup".

    Só retorna após uma janela contínua sem popup.
    """
    registros: list[PopupSap] = []

    wnd = _popup_visivel(page)

    if wnd is None:
        return registros

    sem_popup_desde = None

    for _ in range(max_popups):
        if heartbeat is not None:
            heartbeat.assert_owned()

        wnd = _popup_visivel(page)

        if wnd is not None:
            sem_popup_desde = None
            registro = _ler_popup(wnd)
            registros.append(registro)

            _clicar_x_mapeado(
                page,
                wnd,
            )

            aguardar_sap_pronto(
                page,
                heartbeat=heartbeat,
                appearance_grace_ms=700,
                stable_ms=40,
            )

            # Não devolve controle ao cabeçalho imediatamente:
            # o Open quotations pode surgir depois do Partner selection.
            page.wait_for_timeout(80)
            continue

        if sem_popup_desde is None:
            sem_popup_desde = time.monotonic()

        # 2,5s contínuos sem popup real = cadeia encerrada.
        if (
            time.monotonic() - sem_popup_desde
        ) >= 2.5:
            return registros

        time.sleep(0.05)

    raise RuntimeError(
        "Mais de 20 popups SAP apareceram em sequência."
    )
