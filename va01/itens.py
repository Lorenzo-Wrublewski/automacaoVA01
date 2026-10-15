from __future__ import annotations

import time

from playwright.sync_api import Page

from app_config import carregar_json
from sap.errors import MapeamentoPendente, PularItem
from sap.fields import (
    clicar,
    preencher,
    selecionar_opcao_texto,
)
from sap.state import identificar_estado
from va01.atp import tratar_availability_control


_SELECTORS = carregar_json("selectors.json")
_ITEM_FIELDS = carregar_json("item_fields.json")


_FIXED_MAP = {
    "material": "material",
    "quantidade": "order_quantity",
    "data_desejada": "first_delivery_date",
    "tipo_uso_material": "usage",
    "material_cliente": "customer_material",
    "centro": "plant",
}

_UNMAPPED_FIXED = {
    "pn00",
    "desconto_campanha_oeg",
    "entrega_total_parcial",
    "moeda",
    "preco",
}


def _params_item(item: dict) -> dict:
    params = {}

    for coluna, chave in _FIXED_MAP.items():
        valor = item.get(coluna)

        if (
            valor is not None
            and str(valor).strip()
        ):
            params[chave] = valor

    pendentes = [
        coluna
        for coluna in _UNMAPPED_FIXED
        if (
            item.get(coluna) is not None
            and str(item.get(coluna)).strip()
        )
    ]

    if pendentes:
        raise MapeamentoPendente(
            "Campos SQL preenchidos ainda sem "
            "locator validado: "
            + ", ".join(sorted(pendentes))
        )

    extras = item.get(
        "parametros_json"
    ) or {}

    if isinstance(extras, dict):
        params.update(extras)

    return params


def _preencher_parametro(
    page: Page,
    runtime,
    chave: str,
    valor,
) -> None:
    config = _ITEM_FIELDS[
        "fields"
    ].get(chave)

    if config is None:
        raise MapeamentoPendente(
            f"Campo {chave!r} sem mapeamento."
        )

    inicio = time.monotonic()

    runtime.logger(
        f"[ITEM] Preenchendo {chave} = {valor}"
    )

    tipo = config["kind"]
    spec = config["locator"]

    if tipo == "text":
        preencher(
            page,
            spec,
            valor,
            heartbeat=runtime.heartbeat,
        )

    elif tipo == "dropdown":
        selecionar_opcao_texto(
            page,
            spec,
            str(valor),
            heartbeat=runtime.heartbeat,
        )

    else:
        raise MapeamentoPendente(
            f"Tipo não suportado: "
            f"{chave}/{tipo}"
        )

    runtime.logger(
        f"[PERF] campo {chave}: "
        f"{time.monotonic() - inicio:.2f}s"
    )


def _contar_material_na_grade(
    page: Page,
    material: str,
) -> int:
    """
    Conta quantas LINHAS da grade "All items" contêm o material.

    Isso é essencial quando a OV possui dois itens com o MESMO material.
    A antiga validação booleana ("material existe na grade?") dava falso
    positivo no item 2 porque o item 1 já estava lá.
    """
    try:
        return int(
            page.evaluate(
                """
                material => {
                    const visible = el => {
                        if (!el) return false;
                        const s = getComputedStyle(el);
                        const r = el.getBoundingClientRect();

                        return (
                            s.display !== 'none' &&
                            s.visibility !== 'hidden' &&
                            r.width > 0 &&
                            r.height > 0
                        );
                    };

                    const all = [...document.querySelectorAll('*')]
                        .find(
                            el =>
                                visible(el) &&
                                (el.textContent || '').trim() ===
                                    'All items'
                        );

                    if (!all) return 0;

                    const y = all.getBoundingClientRect().bottom;

                    // Procura por linhas reais da tabela abaixo de "All items".
                    // Conta no máximo uma ocorrência por <tr>.
                    const rows = [
                        ...document.querySelectorAll('tr')
                    ].filter(row => {
                        if (!visible(row)) return false;

                        const rr = row.getBoundingClientRect();

                        if (rr.top <= y) return false;

                        return [
                            ...row.querySelectorAll('td, input, span, div, a')
                        ].some(el => {
                            if (!visible(el)) return false;

                            const value = (
                                ('value' in el && el.value) ||
                                el.textContent ||
                                ''
                            ).trim();

                            return value === material;
                        });
                    });

                    if (rows.length) {
                        return rows.length;
                    }

                    // Fallback para layouts SAP sem <tr> convencional:
                    // conta elementos exatos, deduplicando pela posição vertical.
                    const ys = [];

                    for (const el of document.querySelectorAll(
                        'td, input, span, div, a'
                    )) {
                        if (!visible(el)) continue;

                        const r = el.getBoundingClientRect();
                        if (r.top <= y) continue;

                        const value = (
                            ('value' in el && el.value) ||
                            el.textContent ||
                            ''
                        ).trim();

                        if (value !== material) continue;

                        const rowY = Math.round(r.top);

                        if (!ys.some(existing => Math.abs(existing - rowY) <= 2)) {
                            ys.push(rowY);
                        }
                    }

                    return ys.length;
                }
                """,
                material,
            )
        )
    except Exception:
        return 0


def _item_na_grade(
    page: Page,
    material: str,
) -> bool:
    return _contar_material_na_grade(
        page,
        material,
    ) > 0


def abrir_item_detail(
    page: Page,
    runtime,
) -> None:
    runtime.etapa("ITEM_DETAIL")

    clicar(
        page,
        _SELECTORS[
            "overview"
        ]["item_detail_tab"],
        heartbeat=runtime.heartbeat,
    )

    runtime.estabilizar(
        contexto="item",
        appearance_grace_ms=80,
    )


def _finalizar_criacao_item(
    page: Page,
    runtime,
    material: str,
    *,
    quantidade_antes: int,
    timeout_sem_loading_s: float = 8.0,
) -> bool:
    """
    Máquina de transição pós-Create.

    Pode acontecer:
    Create
      -> loading
      -> popup
      -> warning no rodapé
      -> Enter
      -> loading
      -> Availability Control
      -> Complete delivery
      -> Overview
      -> item na grade

    O código não presume que haverá apenas UMA dessas etapas.
    """
    limite = (
        time.monotonic()
        + timeout_sem_loading_s
    )

    while time.monotonic() < limite:
        try:
            runtime.estabilizar(
                contexto="item",
                # Após Create a reação pode vir como:
                # loading, popup, rodapé, ATP ou item na grade.
                # Observa tudo dinamicamente por até 2s,
                # mas retorna antes se o item for confirmado.
                appearance_grace_ms=2000,
                auto_reenter=True,
                confirmar=lambda: (
                    _contar_material_na_grade(
                        page,
                        material,
                    ) > quantidade_antes
                ),
            )
        except PularItem:
            runtime.logger(
                f"[ITEM] Pulado: {material}"
            )
            return False

        estado = identificar_estado(page)

        if estado == "AVAILABILITY_CONTROL":
            runtime.logger(
                "[ITEM] Availability Control detectado."
            )

            tratar_availability_control(
                page,
                runtime,
            )

            # ATP já executou uma ação; reinicia uma janela curta
            # para observar o retorno.
            limite = (
                time.monotonic()
                + timeout_sem_loading_s
            )
            continue

        if (
            _contar_material_na_grade(
                page,
                material,
            ) > quantidade_antes
        ):
            return True

        # Sem loading/popup/warning e sem item ainda:
        # apenas polling curto, não sleeps de segundos.
        page.wait_for_timeout(80)

    return (
        _contar_material_na_grade(
            page,
            material,
        ) > quantidade_antes
    )


def criar_item(
    page: Page,
    runtime,
    item: dict,
) -> bool:
    params = _params_item(item)

    material = str(
        params.get("material", "")
    ).strip()

    if not material:
        raise RuntimeError(
            "Item sem material."
        )

    runtime.etapa(
        f"ITEM:{item.get('sequencia') or '?'}"
    )

    for chave, valor in params.items():
        _preencher_parametro(
            page,
            runtime,
            chave,
            valor,
        )

    # Snapshot da grade ANTES do Create.
    # Se o mesmo material já existir em outro item, o novo item só é
    # considerado criado quando a quantidade de linhas desse material aumentar.
    quantidade_antes = _contar_material_na_grade(
        page,
        material,
    )

    runtime.logger(
        "[ITEM] Campos preenchidos. Clicando Create. "
        f"Ocorrências atuais na grade: {quantidade_antes}"
    )

    clicar(
        page,
        _SELECTORS[
            "overview"
        ]["create_item"],
        heartbeat=runtime.heartbeat,
    )

    if not _finalizar_criacao_item(
        page,
        runtime,
        material,
        quantidade_antes=quantidade_antes,
    ):
        # Regra do eSales antigo:
        # - item inválido em OV com vários itens: pula este item e segue;
        # - se nenhum item válido restar, processar_itens() encerra a OV em erro.
        runtime.logger(
            f"[ITEM] Ignorado após validação SAP: {material}"
        )
        return False

    runtime.logger(
        f"[ITEM] Criado: {material}"
    )

    return True


def processar_itens(
    page: Page,
    runtime,
    solicitacao: dict,
) -> None:
    itens = solicitacao.get(
        "itens"
    ) or []

    if not itens:
        raise RuntimeError(
            "Solicitação sem itens."
        )

    abrir_item_detail(
        page,
        runtime,
    )

    criados = 0

    for item in itens:
        if criar_item(
            page,
            runtime,
            item,
        ):
            criados += 1

    if criados == 0:
        raise RuntimeError(
            "Nenhum item criado."
        )
