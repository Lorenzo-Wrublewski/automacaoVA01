from __future__ import annotations

from playwright.sync_api import Page

from app_config import carregar_json
from sap.fields import clicar
from sap.messages import classificar
from sap.status_bar import aguardar_novo_status, ler_status


_SELECTORS = carregar_json("selectors.json")


def salvar_ov(
    page: Page,
    runtime,
) -> tuple[str, str]:
    runtime.etapa("SALVANDO")

    anterior_obj = ler_status(page)
    anterior = (
        anterior_obj.texto
        if anterior_obj is not None
        else ""
    )

    clicar(
        page,
        _SELECTORS["toolbar"]["save"],
        heartbeat=runtime.heartbeat,
    )

    resultado = runtime.estabilizar(
        contexto="salvamento",
        appearance_grace_ms=200,
    )

    if resultado.ov:
        mensagem = (
            resultado.status.texto
            if resultado.status
            else ""
        )
        return resultado.ov, mensagem

    # Depois de o loading acabar, a mensagem de Save deve surgir
    # rapidamente. Não fica 15s parado sem motivo.
    status = aguardar_novo_status(
        page,
        anterior,
        heartbeat=runtime.heartbeat,
        timeout_ms=3000,
    )

    if status is None:
        raise RuntimeError(
            "Save executado, mas nenhuma mensagem "
            "de rodapé foi capturada."
        )

    runtime._registrar(status.texto)

    decisao = classificar(
        status.texto,
        contexto="salvamento",
        tipo_status=status.tipo,
    )

    runtime._aplicar_decisao(decisao)

    if not decisao.ov:
        raise RuntimeError(
            "Save não retornou mensagem de sucesso "
            f"com número da OV: {status.texto}"
        )

    return decisao.ov, status.texto
