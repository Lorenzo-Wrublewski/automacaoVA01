from __future__ import annotations

import re
from dataclasses import dataclass

from app_config import carregar_json


_CONFIG = carregar_json("message_handlers.json")


@dataclass
class DecisaoMensagem:
    id: str
    acao: str
    severidade: str
    descricao: str
    texto: str
    ov: str | None = None


def classificar(
    texto: str,
    *,
    contexto: str,
    tipo_status: str = "unknown",
) -> DecisaoMensagem:
    texto = (texto or "").strip()

    for regra in _CONFIG["mensagens"]:
        regra_contexto = regra.get("contexto")

        if (
            regra_contexto
            and regra_contexto not in {contexto, "*"}
        ):
            continue

        padrao = regra["padrao_texto"]

        if regra.get("tipo_regex", False):
            match = re.search(
                padrao,
                texto,
                flags=re.IGNORECASE | re.DOTALL,
            )
        else:
            match = (
                object()
                if padrao.casefold() in texto.casefold()
                else None
            )

        if not match:
            continue

        ov = None

        if (
            regra.get("acao") == "capturar_ov"
            and hasattr(match, "groups")
            and match.groups()
        ):
            ov = match.group(1)

        return DecisaoMensagem(
            id=regra["id"],
            acao=regra["acao"],
            severidade=regra["severidade"],
            descricao=regra.get("descricao", ""),
            texto=texto,
            ov=ov,
        )

    # Fallback conservador:
    # erro/warning desconhecido NÃO é ignorado silenciosamente.
    if tipo_status in {"error", "warning"}:
        acao = "erro_solicitacao"
        severidade = tipo_status
    else:
        acao = "ignorar"
        severidade = tipo_status

    return DecisaoMensagem(
        id="UNKNOWN",
        acao=acao,
        severidade=severidade,
        descricao="Mensagem SAP não catalogada",
        texto=texto,
    )
