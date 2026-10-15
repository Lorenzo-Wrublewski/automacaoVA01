from __future__ import annotations

import time
from dataclasses import dataclass

from playwright.sync_api import Page


@dataclass
class PopupSap:
    texto: str
    titulo: str


def _snapshot_popup(page: Page) -> dict | None:
    """
    Detecta qualquer popup SAP REAL pelo X visível.

    Não depende do programa SAP que gerou o popup:
      SAPMSSY...
      SAPLSPO...
      SAPMSDYP...
      etc.

    Regra:
      qualquer elemento visível cujo id termine em "-close".
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
                    const m = el.id.match(/_(\\d+)-close$/);

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


def popup_visivel(page: Page) -> bool:
    return _snapshot_popup(page) is not None


def _fingerprint(snap: dict | None) -> str:
    if snap is None:
        return ""

    return "|".join(
        (
            str(snap.get("close_id") or ""),
            str(snap.get("title") or ""),
            str(snap.get("text") or "")[:1000],
        )
    )


def _aguardar_mudar_ou_sumir(
    page: Page,
    *,
    fingerprint_anterior: str,
    heartbeat=None,
    timeout_ms: int,
) -> dict | None:
    """
    Confirma continuamente que o popup:
      - sumiu; ou
      - foi substituído por outro.

    Retorna imediatamente quando isso acontecer.
    """
    limite = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < limite:
        if heartbeat is not None:
            heartbeat.assert_owned()

        atual = _snapshot_popup(page)

        if atual is None:
            return None

        if _fingerprint(atual) != fingerprint_anterior:
            return atual

        time.sleep(0.025)

    return _snapshot_popup(page)


def _clicar_x_real(
    page: Page,
    snap: dict,
    *,
    heartbeat=None,
) -> dict | None:
    """
    Fecha o popup de forma robusta.

    Motivo desta implementação:
    SAP WebGUI nem sempre reage a `element.click()` executado via JS.
    O método pode retornar sucesso, mas o evento interno do SAP não é
    processado — foi exatamente o caso do SAPMSDYP10_1-close.

    Estratégia:
      1. Playwright click REAL no ID exato;
      2. confirma por 300 ms se o popup sumiu/trocou;
      3. se ainda estiver aberto, usa ESC como fallback genérico;
      4. confirma por até 2 s;
      5. se necessário, último fallback com mouse físico no centro do X.

    ESC só é enviado quando um popup REAL continua visível.
    """
    close_id = str(
        snap.get("close_id") or ""
    )

    if not close_id:
        raise RuntimeError(
            "Popup SAP detectado sem ID do botão X."
        )

    fp = _fingerprint(snap)

    # ----------------------------------------------------------
    # 1) CLICK REAL DO PLAYWRIGHT
    # ----------------------------------------------------------
    try:
        x = page.locator(
            f'[id="{close_id}"]'
        )

        if (
            x.count() > 0
            and x.first.is_visible(timeout=100)
        ):
            x.first.click(
                timeout=800,
                force=True,
            )
    except Exception:
        pass

    atual = _aguardar_mudar_ou_sumir(
        page,
        fingerprint_anterior=fp,
        heartbeat=heartbeat,
        timeout_ms=300,
    )

    if (
        atual is None
        or _fingerprint(atual) != fp
    ):
        return atual

    # ----------------------------------------------------------
    # 2) FALLBACK GENÉRICO: ESC
    # ----------------------------------------------------------
    # Só chegamos aqui porque o MESMO popup real ainda está aberto.
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass

    atual = _aguardar_mudar_ou_sumir(
        page,
        fingerprint_anterior=fp,
        heartbeat=heartbeat,
        timeout_ms=1200,
    )

    if (
        atual is None
        or _fingerprint(atual) != fp
    ):
        return atual

    # ----------------------------------------------------------
    # 3) ÚLTIMO FALLBACK: CLICK FÍSICO PELO CENTRO DO X
    # ----------------------------------------------------------
    try:
        x = page.locator(
            f'[id="{close_id}"]'
        )

        if (
            x.count() > 0
            and x.first.is_visible(timeout=100)
        ):
            box = x.first.bounding_box()

            if box:
                page.mouse.click(
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
    except Exception:
        pass

    atual = _aguardar_mudar_ou_sumir(
        page,
        fingerprint_anterior=fp,
        heartbeat=heartbeat,
        timeout_ms=800,
    )

    if (
        atual is None
        or _fingerprint(atual) != fp
    ):
        return atual

    raise RuntimeError(
        "Popup SAP continuou aberto após "
        "click real + ESC + click físico. "
        f"Título={snap.get('title')!r}; "
        f"X={close_id!r}"
    )


def fechar_todos_popups(
    page: Page,
    *,
    heartbeat=None,
    contexto: str = "",
    max_popups: int = 20,
) -> list[PopupSap]:
    """
    Drena qualquer cadeia de popups SAP.

    Sem espera fixa de silêncio:
      popup apareceu -> fecha -> confirma;
      outro apareceu -> fecha;
      nenhum popup -> retorna imediatamente.
    """
    registros: list[PopupSap] = []

    snap = _snapshot_popup(page)

    for _ in range(max_popups):
        if heartbeat is not None:
            heartbeat.assert_owned()

        if snap is None:
            return registros

        registros.append(
            PopupSap(
                texto=str(
                    snap.get("text") or ""
                ),
                titulo=str(
                    snap.get("title") or ""
                ),
            )
        )

        snap = _clicar_x_real(
            page,
            snap,
            heartbeat=heartbeat,
        )

    raise RuntimeError(
        "Mais de 20 popups SAP apareceram em sequência."
    )
