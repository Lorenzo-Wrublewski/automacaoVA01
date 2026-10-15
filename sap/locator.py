from __future__ import annotations

from playwright.sync_api import Locator, Page


FAST_LOOKUP_TIMEOUT_MS = 2500


def _xpath_literal(texto: str) -> str:
    if "'" not in texto:
        return f"'{texto}'"

    if '"' not in texto:
        return f'"{texto}"'

    partes = texto.split("'")
    return "concat(" + ", \"'\", ".join(
        f"'{parte}'" for parte in partes
    ) + ")"


def _primeiro(locator: Locator) -> Locator:
    return locator.first


def resolver(page: Page, spec: dict) -> Locator:
    tipo = spec["type"]

    if tipo == "role":
        return _primeiro(
            page.get_by_role(
                spec["role"],
                name=spec.get("name"),
                exact=spec.get("exact", False),
            )
        )

    if tipo == "title":
        return _primeiro(
            page.get_by_title(
                spec["title"],
                exact=spec.get("exact", False),
            )
        )

    if tipo == "text":
        return _primeiro(
            page.get_by_text(
                spec["text"],
                exact=spec.get("exact", False),
            )
        )

    if tipo == "css":
        return _primeiro(
            page.locator(spec["selector"])
        )

    if tipo == "xpath":
        return _primeiro(
            page.locator(
                f'xpath={spec["selector"]}'
            )
        )

    if tipo == "id":
        value = spec["id"].replace('"', '\\"')
        return _primeiro(
            page.locator(
                f'[id="{value}"]'
            )
        )

    if tipo == "label_input":
        label = _xpath_literal(
            spec["label"]
        )
        label_index = int(
            spec.get("label_index", 1)
        )
        input_index = int(
            spec.get("input_index", 1)
        )

        xpath = (
            f"(//*[normalize-space(text())={label}])"
            f"[{label_index}]"
            f"/following::input"
            f"[not(@type='hidden')]"
            f"[{input_index}]"
        )

        return _primeiro(
            page.locator(
                f"xpath={xpath}"
            )
        )

    raise ValueError(
        f"Tipo de locator não suportado: {tipo}"
    )


def resolver_candidatos(
    page: Page,
    specs: list[dict],
) -> Locator:
    ultimo = None

    for spec in specs:
        locator = resolver(page, spec)
        ultimo = locator

        try:
            # count() não espera timeout grande.
            if locator.count() > 0:
                return locator
        except Exception:
            continue

    if ultimo is None:
        raise RuntimeError(
            "Lista de seletores vazia."
        )

    return ultimo


def visivel(page: Page, spec: dict) -> bool:
    try:
        locator = resolver(page, spec)

        if locator.count() == 0:
            return False

        return locator.is_visible(
            timeout=300
        )
    except Exception:
        return False


def esperar_visivel(
    page: Page,
    spec: dict,
    timeout_ms: int = FAST_LOOKUP_TIMEOUT_MS,
) -> Locator:
    locator = resolver(page, spec)
    locator.wait_for(
        state="visible",
        timeout=timeout_ms,
    )
    return locator
