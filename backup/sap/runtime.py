from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Page

from database.repository import registrar_mensagem
from sap.errors import (
    AbortarSolicitacao,
    PularItem,
    SapMessageError,
)
from sap.loading import aguardar_sap_pronto
from sap.messages import DecisaoMensagem, classificar
from sap.popups import fechar_todos_popups
from sap.state import identificar_estado
from sap.status_bar import StatusSap, ler_status


@dataclass
class ResultadoEstabilizacao:
    estado: str
    status: StatusSap | None
    decisoes: list[DecisaoMensagem]

    @property
    def precisa_reenter(self) -> bool:
        return any(
            d.acao == "reenter"
            for d in self.decisoes
        )

    @property
    def ov(self) -> str | None:
        for decisao in self.decisoes:
            if decisao.ov:
                return decisao.ov

        return None


class SapRuntime:
    def __init__(
        self,
        page: Page,
        heartbeat,
        *,
        solicitacao_id: int,
        executor_id: int,
        lock_token: str,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.page = page
        self.heartbeat = heartbeat
        self.solicitacao_id = solicitacao_id
        self.executor_id = executor_id
        self.lock_token = lock_token
        self.logger = logger
        self._last_status_text = ""

        self._etapa_atual = None
        self._etapa_inicio = time.monotonic()

    def etapa(self, nome: str) -> None:
        agora = time.monotonic()

        if self._etapa_atual is not None:
            self.logger(
                f"[PERF] {self._etapa_atual}: "
                f"{agora - self._etapa_inicio:.2f}s"
            )

        self._etapa_atual = nome
        self._etapa_inicio = agora

        self.heartbeat.set_etapa(nome)
        self.logger(f"[ETAPA] {nome}")

    def _registrar(
        self,
        texto: str,
        *,
        persistir_sql: bool = False,
    ) -> None:
        if not texto:
            return

        self.logger(f"[SAP] {texto}")

        if persistir_sql:
            registrar_mensagem(
                self.solicitacao_id,
                self.executor_id,
                self.lock_token,
                texto,
            )

    def _aplicar_decisao(
        self,
        decisao: DecisaoMensagem,
    ) -> None:
        acao = decisao.acao

        if acao in {
            "ignorar",
            "confirmar_continuar",
            "reenter",
            "capturar_ov",
        }:
            return

        if acao == "pular_item":
            raise PularItem(decisao.texto)

        if acao in {
            "pular_ov",
            "abortar",
            "erro_solicitacao",
            "investigar",
        }:
            raise AbortarSolicitacao(
                f"{decisao.id}: {decisao.texto}"
            )

        raise SapMessageError(
            f"Ação de mensagem desconhecida: {acao}"
        )

    def estabilizar(
        self,
        *,
        contexto: str,
        appearance_grace_ms: int = 100,
        auto_reenter: bool = False,
        max_reenter: int = 4,
    ) -> ResultadoEstabilizacao:
        for _ in range(max_reenter):
            self.heartbeat.assert_owned()

            aguardar_sap_pronto(
                self.page,
                heartbeat=self.heartbeat,
                appearance_grace_ms=appearance_grace_ms,
            )

            decisoes: list[DecisaoMensagem] = []

            popups = fechar_todos_popups(
                self.page,
                heartbeat=self.heartbeat,
                contexto=contexto,
            )

            for popup in popups:
                texto = popup.texto.strip()

                if not texto:
                    continue

                self._registrar(texto)

                decisao = classificar(
                    texto,
                    contexto=contexto,
                    tipo_status=(
                        "info"
                        if "information"
                        in popup.titulo.casefold()
                        else "unknown"
                    ),
                )

                decisoes.append(decisao)
                self._aplicar_decisao(decisao)

            status = ler_status(self.page)

            if (
                status is not None
                and status.texto
                != self._last_status_text
            ):
                self._last_status_text = status.texto
                self._registrar(status.texto)

                decisao = classificar(
                    status.texto,
                    contexto=contexto,
                    tipo_status=status.tipo,
                )

                decisoes.append(decisao)
                self._aplicar_decisao(decisao)

            resultado = ResultadoEstabilizacao(
                estado=identificar_estado(
                    self.page
                ),
                status=status,
                decisoes=decisoes,
            )

            if (
                auto_reenter
                and resultado.precisa_reenter
            ):
                self.logger(
                    "[SAP] Mensagem exige confirmação: Enter"
                )

                self._last_status_text = ""
                self.page.keyboard.press("Enter")
                appearance_grace_ms = 120
                continue

            return resultado

        raise RuntimeError(
            "SAP pediu confirmação repetidamente "
            "sem avançar."
        )
