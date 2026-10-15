from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from playwright.sync_api import Locator, Page

from sap.loading import aguardar_sap_pronto
from sap.locator import resolver, resolver_candidatos


# NÃO deixar erro de locator travar 15/30 segundos.
FAST_ACTION_TIMEOUT_MS = 2500


def formatar_valor(valor) -> str:
    if valor is None:
        return ""

    if isinstance(valor, datetime):
        return valor.strftime("%m/%d/%Y")

    if isinstance(valor, date):
        return valor.strftime("%m/%d/%Y")

    if isinstance(valor, Decimal):
        texto = format(valor, "f")
        return texto.rstrip("0").rstrip(".") or "0"

    return str(valor)


def _normalizar(texto: str) -> str:
    return " ".join(
        str(texto or "").replace("\xa0", " ").split()
    ).strip()


def _tag(locator: Locator) -> str:
    try:
        return locator.evaluate(
            "(el) => el.tagName.toLowerCase()"
        )
    except Exception:
        return ""


def _alvo_clicavel(locator: Locator) -> Locator:
    try:
        tag = _tag(locator)

        if tag in {"button", "a", "input"}:
            return locator

        ct = locator.get_attribute("ct")
        role = locator.get_attribute("role")

        if ct == "B" or role == "button":
            return locator

        ancestor = locator.locator(
            "xpath=ancestor::*[self::button or self::a or "
            "@role='button' or @ct='B'][1]"
        )

        if ancestor.count():
            return ancestor.first
    except Exception:
        pass

    return locator


def _editor_real(
    page: Page,
    locator: Locator,
) -> Locator | None:
    tag = _tag(locator)

    if tag in {"input", "textarea"}:
        return locator

    filhos = locator.locator(
        "input:not([type='hidden']), textarea, "
        "[contenteditable='true']"
    )

    if filhos.count():
        return filhos.first

    try:
        locator.focus(
            timeout=FAST_ACTION_TIMEOUT_MS
        )
    except Exception:
        pass

    focado = page.locator(
        "input:focus, textarea:focus, "
        "[contenteditable='true']:focus"
    )

    if focado.count():
        return focado.first

    return None


def _valor_dom(locator: Locator) -> str:
    try:
        valor = locator.evaluate(
            """
            el => {
                if ('value' in el) {
                    return String(el.value ?? '');
                }
                return String(el.textContent ?? '');
            }
            """
        )
        return _normalizar(valor)
    except Exception:
        return ""


def preencher(
    page: Page,
    spec: dict | list[dict],
    valor,
    *,
    heartbeat=None,
    tentativas: int = 2,
) -> Locator:
    esperado = formatar_valor(valor).strip()

    locator = (
        resolver_candidatos(page, spec)
        if isinstance(spec, list)
        else resolver(page, spec)
    )

    # O usuário já explicou: campos podem estar fora da viewport,
    # mas existir no DOM. Então NÃO exigimos "visible" aqui.
    locator.wait_for(
        state="attached",
        timeout=FAST_ACTION_TIMEOUT_MS,
    )

    for tentativa in range(1, tentativas + 1):
        if heartbeat is not None:
            heartbeat.assert_owned()

        aguardar_sap_pronto(
            page,
            heartbeat=heartbeat,
            stable_ms=0,
        )

        editor = _editor_real(
            page,
            locator,
        )

        if editor is None:
            raise RuntimeError(
                "Campo existe no DOM, mas nenhum editor "
                "real foi encontrado."
            )

        try:
            editor.fill(
                esperado,
                timeout=FAST_ACTION_TIMEOUT_MS,
            )
        except Exception:
            editor.focus(
                timeout=FAST_ACTION_TIMEOUT_MS
            )
            page.keyboard.press("Control+A")
            page.keyboard.insert_text(esperado)

        atual = _valor_dom(editor)

        if atual == _normalizar(esperado):
            return locator

        if tentativa < tentativas:
            page.wait_for_timeout(80)

    raise RuntimeError(
        "Campo não confirmou valor. "
        f"Esperado={esperado!r}; "
        f"atual={atual!r}"
    )


def clicar(
    page: Page,
    spec: dict | list[dict],
    *,
    heartbeat=None,
) -> Locator:
    aguardar_sap_pronto(
        page,
        heartbeat=heartbeat,
        stable_ms=0,
    )

    locator = (
        resolver_candidatos(page, spec)
        if isinstance(spec, list)
        else resolver(page, spec)
    )

    locator.wait_for(
        state="attached",
        timeout=FAST_ACTION_TIMEOUT_MS,
    )

    alvo = _alvo_clicavel(locator)

    alvo.click(
        timeout=FAST_ACTION_TIMEOUT_MS,
    )

    return locator


def valor(
    page: Page,
    spec: dict | list[dict],
) -> str:
    locator = (
        resolver_candidatos(page, spec)
        if isinstance(spec, list)
        else resolver(page, spec)
    )

    editor = _editor_real(
        page,
        locator,
    )

    if editor is not None:
        return _valor_dom(editor)

    return _valor_dom(locator)


def selecionar_opcao_texto(
    page: Page,
    campo_spec: dict | list[dict],
    texto: str,
    *,
    heartbeat=None,
) -> None:
    clicar(
        page,
        campo_spec,
        heartbeat=heartbeat,
    )

    opcao = page.get_by_text(
        str(texto),
        exact=True,
    )

    for i in range(opcao.count()):
        item = opcao.nth(i)

        try:
            if item.is_visible(timeout=200):
                item.click(
                    timeout=FAST_ACTION_TIMEOUT_MS
                )
                return
        except Exception:
            continue

    raise RuntimeError(
        f"Opção {texto!r} não encontrada no dropdown."
    )
