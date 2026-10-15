from __future__ import annotations

import threading

from app_config import HEARTBEAT_INTERVAL_SECONDS
from database.repository import heartbeat


class HeartbeatWorker:
    """
    Heartbeat roda 100% em background.

    IMPORTANTE:
    set_etapa() NÃO acessa mais o SQL de forma síncrona.
    Antes, cada mudança de etapa podia bloquear o fluxo principal
    enquanto abria conexão/atualizava o banco, causando pausas
    perceptíveis antes de VA01, YSTD, Partners etc.
    """

    def __init__(
        self,
        solicitacao_id: int,
        executor_id: int,
        lock_token: str,
    ) -> None:
        self.solicitacao_id = solicitacao_id
        self.executor_id = executor_id
        self.lock_token = lock_token

        self._etapa = "INICIALIZANDO"
        self._lock = threading.Lock()

        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lost = threading.Event()

        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name=f"heartbeat-va01-{executor_id}",
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        self._thread.join(timeout=2)

    def set_etapa(self, etapa: str) -> None:
        with self._lock:
            self._etapa = etapa

        # Apenas acorda a thread de heartbeat.
        # NÃO bloqueia a automação esperando SQL.
        self._wake.set()

    def pulse(self) -> None:
        with self._lock:
            etapa = self._etapa

        ok = heartbeat(
            self.solicitacao_id,
            self.executor_id,
            self.lock_token,
            etapa,
        )

        if not ok:
            self._lost.set()

    def assert_owned(self) -> None:
        if self._lost.is_set():
            raise RuntimeError(
                "Posse SQL da solicitação foi perdida."
            )

    def _run(self) -> None:
        while not self._stop.is_set():
            # Atualiza no máximo a cada HEARTBEAT_INTERVAL_SECONDS,
            # mas uma troca de etapa pode acordar a thread antes.
            self._wake.wait(
                timeout=HEARTBEAT_INTERVAL_SECONDS
            )
            self._wake.clear()

            if self._stop.is_set():
                break

            try:
                self.pulse()
            except Exception:
                # Falha transitória de conexão não congela o SAP.
                # Perda real de posse é detectada quando UPDATE retorna 0.
                pass
