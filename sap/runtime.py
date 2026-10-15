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
from sap.popups import fechar_todos_popups, popup_visivel
from sap.state import identificar_estado
from sap.status_bar import StatusSap, ler_status


@dataclass
class ResultadoEstabilizacao:
    estado: str
    status: StatusSap | None
    decisoes: list[DecisaoMensagem]
    confirmado: bool = False

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

    def _resultado(
        self,
        *,
        status: StatusSap | None,
        decisoes: list[DecisaoMensagem],
        confirmado: bool = False,
    ) -> ResultadoEstabilizacao:
        return ResultadoEstabilizacao(
            estado=identificar_estado(
                self.page
            ),
            status=status,
            decisoes=decisoes,
            confirmado=confirmado,
        )

    def estabilizar(
        self,
        *,
        contexto: str,
        appearance_grace_ms: int = 100,
        auto_reenter: bool = False,
        max_reenter: int = 4,
        confirmar: Callable[[], bool] | None = None,
    ) -> ResultadoEstabilizacao:
        """
        Observador central de reação do SAP.

        Ele NÃO executa:
            espera loader -> espera popup -> espera status
        em série.

        Em vez disso, durante uma janela curta ele verifica
        continuamente e de forma dinâmica:

          - confirmação da ação;
          - loader;
          - popup;
          - mensagem do rodapé;
          - estado atual.

        Quando nada acontece dentro da janela, retorna e o fluxo
        segue. Se a confirmação da ação for detectada antes,
        retorna imediatamente.

        appearance_grace_ms é apenas o tempo máximo para uma
        reação ASSÍNCRONA aparecer. Não é sleep.
        """
        grace_s = max(
            0,
            appearance_grace_ms,
        ) / 1000

        decisoes: list[DecisaoMensagem] = []
        ultimo_status = ler_status(
            self.page
        )

        deadline = (
            time.monotonic()
            + grace_s
        )

        reenters = 0

        while True:
            self.heartbeat.assert_owned()

            # 0) A própria confirmação da ação tem prioridade.
            # Ex.: item apareceu na grade.
            if confirmar is not None:
                try:
                    if confirmar():
                        return self._resultado(
                            status=ultimo_status,
                            decisoes=decisoes,
                            confirmado=True,
                        )
                except Exception:
                    pass

            # 1) LOADING
            #
            # Sem appearance grace aqui: o loop já é quem observa
            # se ele aparece. Se estiver visível, aguardamos SUMIR.
            antes = time.monotonic()

            aguardar_sap_pronto(
                self.page,
                heartbeat=self.heartbeat,
                appearance_grace_ms=0,
                stable_ms=30,
            )

            demorou_loading = (
                time.monotonic() - antes
            ) >= 0.06

            if demorou_loading:
                # Depois de um loading pode surgir popup/status.
                # Abre nova janela de reação a partir de agora.
                deadline = (
                    time.monotonic()
                    + grace_s
                )

            # 2) POPUP
            if popup_visivel(
                self.page
            ):
                popups = fechar_todos_popups(
                    self.page,
                    heartbeat=self.heartbeat,
                    contexto=contexto,
                )

                for popup in popups:
                    texto = popup.texto.strip()

                    if not texto:
                        continue

                    self._registrar(
                        texto
                    )

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

                    decisoes.append(
                        decisao
                    )
                    self._aplicar_decisao(
                        decisao
                    )

                # Outro popup/status pode vir depois de fechar esse.
                deadline = (
                    time.monotonic()
                    + grace_s
                )

                continue

            # 3) RODAPÉ
            status = ler_status(
                self.page
            )
            ultimo_status = status

            nova_decisao = None

            if (
                status is not None
                and status.texto
                and status.texto
                != self._last_status_text
            ):
                self._last_status_text = (
                    status.texto
                )

                self._registrar(
                    status.texto
                )

                nova_decisao = classificar(
                    status.texto,
                    contexto=contexto,
                    tipo_status=status.tipo,
                )

                decisoes.append(
                    nova_decisao
                )

                self._aplicar_decisao(
                    nova_decisao
                )

                deadline = (
                    time.monotonic()
                    + grace_s
                )

            if (
                auto_reenter
                and nova_decisao is not None
                and nova_decisao.acao == "reenter"
            ):
                reenters += 1

                if reenters > max_reenter:
                    raise RuntimeError(
                        "SAP pediu confirmação repetidamente "
                        "sem avançar."
                    )

                self.logger(
                    "[SAP] Mensagem exige confirmação: Enter"
                )

                self._last_status_text = ""
                self.page.keyboard.press(
                    "Enter"
                )

                deadline = (
                    time.monotonic()
                    + grace_s
                )

                continue

            # Confirma novamente depois de tratar as reações.
            if confirmar is not None:
                try:
                    if confirmar():
                        return self._resultado(
                            status=ultimo_status,
                            decisoes=decisoes,
                            confirmado=True,
                        )
                except Exception:
                    pass

            # Sem janela de observação: resultado imediato.
            if grace_s <= 0:
                return self._resultado(
                    status=ultimo_status,
                    decisoes=decisoes,
                )

            # Nada novo apareceu até o limite.
            if time.monotonic() >= deadline:
                return self._resultado(
                    status=ultimo_status,
                    decisoes=decisoes,
                )

            # Polling curto. Não é espera fixa de negócio.
            time.sleep(0.03)
