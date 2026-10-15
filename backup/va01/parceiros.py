from __future__ import annotations

from playwright.sync_api import Page

from app_config import carregar_json
from sap.errors import MapeamentoPendente, SapStateError
from sap.fields import clicar
from sap.loading import aguardar_sap_pronto
from sap.state import aguardar_estado, identificar_estado


_SELECTORS = carregar_json("selectors.json")
_PARTNER_MAP = carregar_json("partner_map.json")

FAST_TIMEOUT_MS = 1200


def _grade(page: Page) -> tuple[str, list[dict]]:
    """
    Lê TODA a grade Partners em uma única ida ao browser.
    Antes eram dezenas de chamadas Playwright por leitura.
    """
    dados = page.locator(
        '[id^="tbl"][id$=",1]_c"]'
    ).evaluate_all(
        """
        els => {
            const grupos = {};

            const valor = el => {
                if (!el) return '';
                if ('value' in el) {
                    return String(el.value ?? '').trim();
                }
                return String(el.textContent ?? '').trim();
            };

            const visivel = el => {
                if (!el) return false;
                const st = getComputedStyle(el);
                return (
                    st.display !== 'none' &&
                    st.visibility !== 'hidden' &&
                    el.getClientRects().length > 0
                );
            };

            for (const el of els) {
                if (!visivel(el)) continue;

                const m = el.id.match(
                    /^(.*)\\[(\\d+),1\\]_c$/
                );

                if (!m) continue;

                const prefix = m[1];
                const row = Number(m[2]);

                if (!grupos[prefix]) {
                    grupos[prefix] = [];
                }

                const partner = document.getElementById(
                    `${prefix}[${row},2]_c`
                );

                grupos[prefix].push({
                    row,
                    function: valor(el),
                    partner: valor(partner)
                });
            }

            return grupos;
        }
        """
    )

    if not dados:
        raise RuntimeError(
            "Grade Partners não encontrada."
        )

    prefix = max(
        dados,
        key=lambda p: len(dados[p]),
    )

    linhas = sorted(
        dados[prefix],
        key=lambda x: int(x["row"]),
    )

    return prefix, linhas


def _achar_funcao(
    page: Page,
    funcao: str,
) -> tuple[str, int, str] | None:
    prefix, linhas = _grade(page)

    alvo = funcao.strip().casefold()

    for linha in linhas:
        if (
            str(linha["function"]).strip().casefold()
            == alvo
        ):
            return (
                prefix,
                int(linha["row"]),
                str(linha["partner"] or ""),
            )

    return None


def _primeira_linha_vazia(
    page: Page,
) -> tuple[str, int]:
    prefix, linhas = _grade(page)

    for linha in linhas:
        if not str(
            linha["function"] or ""
        ).strip():
            return prefix, int(linha["row"])

    raise RuntimeError(
        "Nenhuma linha vazia em Partners."
    )


def _campo_funcao(
    page: Page,
    prefix: str,
    row: int,
):
    return page.locator(
        f'[id="{prefix}[{row},1]_c"]'
    )


def _selecionar_funcao(
    page: Page,
    funcao: str,
) -> tuple[str, int]:
    prefix, row = _primeira_linha_vazia(
        page
    )

    campo = _campo_funcao(
        page,
        prefix,
        row,
    )

    campo.focus(
        timeout=FAST_TIMEOUT_MS,
    )

    # Fluxo manual validado:
    # inicial -> SAP autocomplete -> TAB.
    page.keyboard.press(funcao[0])
    page.wait_for_timeout(550)

    atual = _achar_funcao(
        page,
        funcao,
    )

    if atual is None:
        # Se só a inicial não resolveu exatamente,
        # continua a pesquisa pelo restante do texto.
        restante = funcao[1:]

        if restante:
            page.keyboard.type(
                restante,
                delay=20,
            )
            page.wait_for_timeout(450)

    page.keyboard.press("Tab")
    page.wait_for_timeout(180)

    encontrado = _achar_funcao(
        page,
        funcao,
    )

    if encontrado is None:
        raise RuntimeError(
            f"Partner Function {funcao!r} "
            "não foi confirmada."
        )

    return encontrado[0], encontrado[1]


def _preencher_partner(
    page: Page,
    prefix: str,
    row: int,
    codigo: str,
    *,
    foco_no_partner: bool,
) -> None:
    if not foco_no_partner:
        campo = _campo_funcao(
            page,
            prefix,
            row,
        )
        campo.focus(
            timeout=FAST_TIMEOUT_MS,
        )
        page.keyboard.press("Tab")

    page.wait_for_timeout(50)
    page.keyboard.press("Control+A")
    page.keyboard.insert_text(
        str(codigo)
    )
    page.wait_for_timeout(60)
    page.keyboard.press("Enter")


def abrir_partners(
    page: Page,
    runtime,
) -> None:
    runtime.etapa("PARCEIROS_ABRINDO")

    estado = identificar_estado(page)

    if estado == "HEADER_PARTNERS":
        return

    if estado == "OVERVIEW":
        clicar(
            page,
            _SELECTORS[
                "header_details"
            ]["button"],
            heartbeat=runtime.heartbeat,
        )

        runtime.estabilizar(
            contexto="parceiro",
            appearance_grace_ms=100,
        )

        estado = identificar_estado(page)

    if estado == "HEADER_PARTNERS":
        return

    if estado == "HEADER_DATA":
        clicar(
            page,
            _SELECTORS[
                "header_details"
            ]["partners_tab"],
            heartbeat=runtime.heartbeat,
        )

        runtime.estabilizar(
            contexto="parceiro",
            appearance_grace_ms=100,
        )

        aguardar_estado(
            page,
            {"HEADER_PARTNERS"},
            heartbeat=runtime.heartbeat,
        )
        return

    raise SapStateError(
        f"Não foi possível abrir Partners. "
        f"Estado={estado}"
    )


def _parceiros_da_solicitacao(
    solicitacao: dict,
) -> list[tuple[str, str]]:
    saida: list[tuple[str, str]] = []

    parametros_json = (
        solicitacao.get("parametros_json")
        or {}
    )

    for parceiro in parametros_json.get(
        "partners",
        [],
    ):
        funcao = str(
            parceiro.get("function", "")
        ).strip()
        codigo = str(
            parceiro.get("partner", "")
        ).strip()

        if funcao and codigo:
            saida.append(
                (funcao, codigo)
            )

    for coluna, funcao in _PARTNER_MAP[
        "fixed_columns"
    ].items():
        codigo = solicitacao.get(coluna)

        if codigo is None:
            continue

        codigo = str(codigo).strip()

        if not codigo:
            continue

        if not funcao:
            raise MapeamentoPendente(
                f"Campo {coluna!r} possui valor, "
                "mas não tem Partner Function mapeada."
            )

        saida.append(
            (funcao, codigo)
        )

    consolidado = {}

    for funcao, codigo in saida:
        consolidado[
            funcao.casefold()
        ] = (
            funcao,
            codigo,
        )

    return list(
        consolidado.values()
    )


def manter_parceiro(
    page: Page,
    runtime,
    *,
    funcao: str,
    codigo: str,
) -> None:
    runtime.etapa(
        f"PARCEIRO:{funcao}"
    )

    existente = _achar_funcao(
        page,
        funcao,
    )

    if existente is not None:
        prefix, row, atual = existente

        if atual.strip() == str(codigo).strip():
            runtime.logger(
                f"[PARTNER] Já correto: "
                f"{funcao} -> {codigo}"
            )
            return

        _preencher_partner(
            page,
            prefix,
            row,
            codigo,
            foco_no_partner=False,
        )

    else:
        prefix, row = _selecionar_funcao(
            page,
            funcao,
        )

        # O TAB acima já levou ao Partner.
        _preencher_partner(
            page,
            prefix,
            row,
            codigo,
            foco_no_partner=True,
        )

    # Enter pode disparar roundtrip SAP.
    aguardar_sap_pronto(
        page,
        heartbeat=runtime.heartbeat,
        appearance_grace_ms=80,
    )

    runtime.estabilizar(
        contexto="parceiro",
        appearance_grace_ms=80,
    )

    validado = _achar_funcao(
        page,
        funcao,
    )

    if validado is None:
        raise RuntimeError(
            f"Partner Function {funcao!r} "
            "sumiu após preenchimento."
        )

    _, _, final = validado

    if final.strip() != str(codigo).strip():
        raise RuntimeError(
            f"Partner não confirmado. "
            f"{funcao}: esperado={codigo!r}, "
            f"atual={final!r}"
        )

    runtime.logger(
        f"[PARTNER] OK: {funcao} -> {codigo}"
    )


def processar_parceiros(
    page: Page,
    runtime,
    solicitacao: dict,
) -> None:
    parceiros = _parceiros_da_solicitacao(
        solicitacao
    )

    if not parceiros:
        return

    abrir_partners(
        page,
        runtime,
    )

    for funcao, codigo in parceiros:
        manter_parceiro(
            page,
            runtime,
            funcao=funcao,
            codigo=codigo,
        )

    # SAP Back = F3.
    page.keyboard.press("F3")

    runtime.estabilizar(
        contexto="parceiro",
        appearance_grace_ms=100,
    )

    aguardar_estado(
        page,
        {"OVERVIEW"},
        heartbeat=runtime.heartbeat,
    )
