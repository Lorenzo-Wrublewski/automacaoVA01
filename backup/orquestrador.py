from __future__ import annotations

import os
import subprocess
import sys
import time
import tkinter as tk
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import messagebox

from app_config import (
    ORCHESTRATOR_STALE_HEARTBEAT_SECONDS,
)
from database.connection import get_conn
from database.repository import obter_execucao


BASE_DIR = Path(__file__).resolve().parent
SCRIPT_EXECUTOR = BASE_DIR / "codigo.py"
PASTA_LOGS = BASE_DIR / "logs"

QUANTIDADE_EXECUTORES = 6
INTERVALO_MONITORAMENTO_MS = 500
INTERVALO_STATUS_SQL_SECONDS = 5


SQL_RESERVAR = """
DECLARE @lock_token UNIQUEIDENTIFIER = ?;
DECLARE @agora DATETIME2(3) = SYSDATETIME();

BEGIN TRANSACTION;

;WITH ProximaSolicitacao AS
(
    SELECT TOP (1) *
    FROM dbo.VA01_Solicitacao WITH (UPDLOCK, READPAST, ROWLOCK)
    WHERE status = 'PENDING'
      AND (
            disponivel_em IS NULL
            OR disponivel_em <= @agora
          )
    ORDER BY
        prioridade ASC,
        criado_em ASC,
        solicitacao_id ASC
)
UPDATE ProximaSolicitacao
SET
    status = 'CLAIMED',
    executor_id = ?,
    executor_pid = NULL,
    lock_token = @lock_token,
    tentativas = tentativas + 1,
    alocado_em = @agora,
    heartbeat_em = @agora,
    finalizado_em = NULL,
    sap_ov = NULL,
    ultimo_erro = NULL,
    atualizado_em = @agora
OUTPUT
    inserted.solicitacao_id,
    inserted.origem,
    inserted.referencia_origem,
    inserted.pedido_cliente,
    inserted.status,
    inserted.executor_id,
    inserted.lock_token,
    inserted.tentativas;

COMMIT TRANSACTION;
"""


def reservar_proxima(executor_id: int) -> dict | None:
    token = uuid.uuid4()

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            SQL_RESERVAR,
            str(token),
            executor_id,
        )
        row = cur.fetchone()
        conn.commit()

    if row is None:
        return None

    return {
        "solicitacao_id": int(row.solicitacao_id),
        "origem": str(row.origem or ""),
        "referencia_origem": str(
            row.referencia_origem or ""
        ),
        "pedido_cliente": str(
            row.pedido_cliente or ""
        ),
        "lock_token": str(row.lock_token),
    }


def marcar_running(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
    pid: int | None = None,
) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE dbo.VA01_Solicitacao
            SET
                status = 'RUNNING',
                executor_pid = ?,
                iniciado_em = COALESCE(
                    iniciado_em,
                    SYSDATETIME()
                ),
                heartbeat_em = SYSDATETIME(),
                ultima_etapa = 'INICIALIZANDO',
                atualizado_em = SYSDATETIME()
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status = 'CLAIMED'
            """,
            pid,
            solicitacao_id,
            executor_id,
            lock_token,
        )

        ok = cur.rowcount == 1
        conn.commit()

    return ok


def atualizar_pid(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
    pid: int,
) -> bool:
    with get_conn() as conn:
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE dbo.VA01_Solicitacao
            SET
                executor_pid = ?,
                atualizado_em = SYSDATETIME()
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status = 'RUNNING'
            """,
            pid,
            solicitacao_id,
            executor_id,
            lock_token,
        )

        ok = cur.rowcount == 1
        conn.commit()

    return ok


def finalizar_timeout(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
    erro: str,
) -> None:
    with get_conn() as conn:
        conn.cursor().execute(
            """
            UPDATE dbo.VA01_Solicitacao
            SET
                status = 'TIMEOUT',
                ultima_etapa = 'HEARTBEAT_TIMEOUT',
                ultimo_erro = ?,
                finalizado_em = SYSDATETIME(),
                atualizado_em = SYSDATETIME()
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status = 'RUNNING'
            """,
            erro,
            solicitacao_id,
            executor_id,
            lock_token,
        )
        conn.commit()


def finalizar_erro_orquestrador(
    solicitacao_id: int,
    executor_id: int,
    lock_token: str,
    erro: str,
) -> None:
    with get_conn() as conn:
        conn.cursor().execute(
            """
            UPDATE dbo.VA01_Solicitacao
            SET
                status = 'ERROR',
                ultima_etapa = 'ORQUESTRADOR',
                ultimo_erro = ?,
                finalizado_em = SYSDATETIME(),
                atualizado_em = SYSDATETIME()
            WHERE solicitacao_id = ?
              AND executor_id = ?
              AND lock_token = ?
              AND status IN ('CLAIMED', 'RUNNING')
            """,
            erro,
            solicitacao_id,
            executor_id,
            lock_token,
        )
        conn.commit()


def listar_pendentes(limite: int = 50) -> list[dict]:
    with get_conn() as conn:
        rows = conn.cursor().execute(
            f"""
            SELECT TOP ({int(limite)})
                solicitacao_id,
                origem,
                referencia_origem,
                pedido_cliente
            FROM dbo.VA01_Solicitacao
            WHERE status = 'PENDING'
              AND (
                    disponivel_em IS NULL
                    OR disponivel_em <= SYSDATETIME()
                  )
            ORDER BY
                prioridade,
                criado_em,
                solicitacao_id
            """
        ).fetchall()

    return [
        {
            "solicitacao_id": int(r.solicitacao_id),
            "origem": str(r.origem or ""),
            "referencia_origem": str(
                r.referencia_origem or ""
            ),
            "pedido_cliente": str(
                r.pedido_cliente or ""
            ),
        }
        for r in rows
    ]


def contagens() -> dict[str, int]:
    with get_conn() as conn:
        rows = conn.cursor().execute(
            """
            SELECT status, COUNT(*) qtd
            FROM dbo.VA01_Solicitacao
            GROUP BY status
            """
        ).fetchall()

    return {
        str(r.status): int(r.qtd)
        for r in rows
    }


@dataclass
class Executor:
    id: int
    processo: subprocess.Popen | None = None
    solicitacao_id: int | None = None
    lock_token: str | None = None
    referencia: str | None = None
    etapa: str | None = None
    inicio: float | None = None
    ultimo_check_sql: float = 0.0
    livre_desde: float = 0.0
    log_handle: object | None = None

    @property
    def livre(self) -> bool:
        return self.processo is None


class OrquestradorApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(
            "Orquestrador - Parte 3 | VA01"
        )
        self.root.geometry("1280x820")
        self.root.minsize(1100, 720)

        PASTA_LOGS.mkdir(exist_ok=True)

        agora = time.monotonic()

        self.executores = [
            Executor(
                id=i,
                livre_desde=agora,
            )
            for i in range(
                1,
                QUANTIDADE_EXECUTORES + 1,
            )
        ]

        self.encerrando = False
        self.pausado = False

        self.cards = {}
        self.lbl_fila = None
        self.lbl_success = None
        self.listbox_fila = None
        self.txt_log = None
        self.btn_pausa = None
        self.btn_limpar_eventos = None

        self.pendentes_cache = []
        self.contagens_cache = {}

        self._montar_tela()

        with get_conn() as conn:
            conn.cursor().execute(
                "SELECT 1"
            ).fetchone()

        self._log(
            "Orquestrador VA01 iniciado com "
            f"{QUANTIDADE_EXECUTORES} executores."
        )

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self._ao_fechar,
        )
        self.root.after(
            100,
            self._loop,
        )

    def _montar_tela(self) -> None:
        bg = "#f4f5f6"
        card_bg = "#ffffff"
        text = "#1f1f1f"
        muted = "#666666"
        accent = "#007bc0"

        self.root.configure(bg=bg)

        header = tk.Frame(
            self.root,
            bg=bg,
        )
        header.pack(
            fill="x",
            padx=24,
            pady=(20, 8),
        )

        tk.Label(
            header,
            text="Orquestrador · Parte 3",
            font=("Segoe UI", 20, "bold"),
            bg=bg,
            fg=text,
        ).pack(side="left")

        self.btn_pausa = tk.Button(
            header,
            text="Pausar novas alocações",
            command=self._alternar_pausa,
            font=("Segoe UI", 10),
            relief="flat",
            padx=14,
            pady=8,
            cursor="hand2",
            bg="#ffffff",
            fg=text,
        )
        self.btn_pausa.pack(side="right")

        resumo = tk.Frame(
            self.root,
            bg=bg,
        )
        resumo.pack(
            fill="x",
            padx=24,
            pady=(0, 14),
        )

        self.lbl_fila = tk.Label(
            resumo,
            text="Pendentes: 0",
            font=("Segoe UI", 10, "bold"),
            bg=bg,
            fg=accent,
        )
        self.lbl_fila.pack(side="left")

        self.lbl_success = tk.Label(
            resumo,
            text="SUCCESS: 0",
            font=("Segoe UI", 10),
            bg=bg,
            fg=muted,
        )
        self.lbl_success.pack(
            side="left",
            padx=(20, 0),
        )

        tk.Label(
            resumo,
            text=(
                "Heartbeat timeout: "
                f"{ORCHESTRATOR_STALE_HEARTBEAT_SECONDS}s"
            ),
            font=("Segoe UI", 10),
            bg=bg,
            fg=muted,
        ).pack(side="right")

        cards_area = tk.Frame(
            self.root,
            bg=bg,
        )
        cards_area.pack(
            fill="x",
            padx=24,
        )

        # 6 executores em 2 linhas x 3 colunas para manter
        # os cards legíveis.
        for coluna in range(3):
            cards_area.grid_columnconfigure(
                coluna,
                weight=1,
                uniform="executor",
            )

        for executor in self.executores:
            frame = tk.Frame(
                cards_area,
                bg=card_bg,
                highlightthickness=1,
                highlightbackground="#d8dce0",
            )

            linha = (executor.id - 1) // 3
            coluna = (executor.id - 1) % 3

            frame.grid(
                row=linha,
                column=coluna,
                sticky="nsew",
                padx=(
                    0 if coluna == 0 else 8,
                    0,
                ),
                pady=(
                    0 if linha == 0 else 8,
                    0,
                ),
            )

            topo = tk.Frame(
                frame,
                bg=card_bg,
            )
            topo.pack(
                fill="x",
                padx=16,
                pady=(14, 8),
            )

            tk.Label(
                topo,
                text=f"EXECUTOR {executor.id}",
                font=("Segoe UI", 12, "bold"),
                bg=card_bg,
                fg=text,
            ).pack(side="left")

            status = tk.Label(
                topo,
                text="LIVRE",
                font=("Segoe UI", 9, "bold"),
                bg="#dff3e7",
                fg="#116329",
                padx=9,
                pady=4,
            )
            status.pack(side="right")

            solicitacao = tk.Label(
                frame,
                text="Solicitação: —",
                font=("Segoe UI", 10),
                bg=card_bg,
                fg=text,
                anchor="w",
            )
            solicitacao.pack(
                fill="x",
                padx=16,
                pady=(4, 2),
            )

            etapa = tk.Label(
                frame,
                text="Etapa: —",
                font=("Segoe UI", 9),
                bg=card_bg,
                fg=muted,
                anchor="w",
            )
            etapa.pack(
                fill="x",
                padx=16,
                pady=2,
            )

            tempo = tk.Label(
                frame,
                text="Tempo: —",
                font=("Segoe UI", 9),
                bg=card_bg,
                fg=muted,
                anchor="w",
            )
            tempo.pack(
                fill="x",
                padx=16,
                pady=2,
            )

            pid = tk.Label(
                frame,
                text="PID: —",
                font=("Segoe UI", 9),
                bg=card_bg,
                fg=muted,
                anchor="w",
            )
            pid.pack(
                fill="x",
                padx=16,
                pady=(2, 14),
            )

            self.cards[executor.id] = {
                "status": status,
                "solicitacao": solicitacao,
                "etapa": etapa,
                "tempo": tempo,
                "pid": pid,
            }

        conteudo = tk.Frame(
            self.root,
            bg=bg,
        )
        conteudo.pack(
            fill="both",
            expand=True,
            padx=24,
            pady=18,
        )

        fila_frame = tk.Frame(
            conteudo,
            bg=card_bg,
            highlightthickness=1,
            highlightbackground="#d8dce0",
        )
        fila_frame.pack(
            side="left",
            fill="both",
        )
        fila_frame.configure(width=365)
        fila_frame.pack_propagate(False)

        tk.Label(
            fila_frame,
            text="Solicitações PENDING",
            font=("Segoe UI", 11, "bold"),
            bg=card_bg,
            fg=text,
        ).pack(
            anchor="w",
            padx=14,
            pady=(12, 8),
        )

        self.listbox_fila = tk.Listbox(
            fila_frame,
            font=("Consolas", 9),
            bd=0,
            highlightthickness=0,
            activestyle="none",
        )
        self.listbox_fila.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=(0, 10),
        )

        log_frame = tk.Frame(
            conteudo,
            bg=card_bg,
            highlightthickness=1,
            highlightbackground="#d8dce0",
        )
        log_frame.pack(
            side="left",
            fill="both",
            expand=True,
            padx=(12, 0),
        )

        log_header = tk.Frame(
            log_frame,
            bg=card_bg,
        )
        log_header.pack(
            fill="x",
            padx=14,
            pady=(10, 6),
        )

        tk.Label(
            log_header,
            text="Eventos do orquestrador",
            font=("Segoe UI", 11, "bold"),
            bg=card_bg,
            fg=text,
        ).pack(
            side="left",
            anchor="w",
        )

        self.btn_limpar_eventos = tk.Button(
            log_header,
            text="Limpar eventos",
            command=self._limpar_eventos,
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=5,
            cursor="hand2",
            bg="#f4f5f6",
            fg=text,
            activebackground="#e8eaec",
        )
        self.btn_limpar_eventos.pack(
            side="right",
        )

        self.txt_log = tk.Text(
            log_frame,
            font=("Consolas", 9),
            bd=0,
            highlightthickness=0,
            state="disabled",
            wrap="word",
        )
        self.txt_log.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=(0, 10),
        )

    def _loop(self) -> None:
        if self.encerrando:
            return

        try:
            self._verificar_execucoes()

            if not self.pausado:
                self._alocar()

            self._atualizar_cache()
            self._atualizar_tela()

        except Exception as exc:
            self._log(
                "ERRO NO ORQUESTRADOR: "
                f"{type(exc).__name__}: {exc}"
            )

        self.root.after(
            INTERVALO_MONITORAMENTO_MS,
            self._loop,
        )

    def _alocar(self) -> None:
        livres = [
            e
            for e in self.executores
            if e.livre
        ]
        livres.sort(
            key=lambda e: (
                e.livre_desde,
                e.id,
            )
        )

        for executor in livres:
            solicitacao = reservar_proxima(
                executor.id
            )

            if solicitacao is None:
                break

            try:
                self._iniciar(
                    executor,
                    solicitacao,
                )
            except Exception as exc:
                finalizar_erro_orquestrador(
                    solicitacao["solicitacao_id"],
                    executor.id,
                    solicitacao["lock_token"],
                    (
                        "Falha ao iniciar executor: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
                self._log(
                    "FALHA AO INICIAR "
                    f"#{solicitacao['solicitacao_id']}: "
                    f"{exc}"
                )

    def _iniciar(
        self,
        executor: Executor,
        solicitacao: dict,
    ) -> None:
        sid = solicitacao["solicitacao_id"]
        token = solicitacao["lock_token"]

        log_path = (
            PASTA_LOGS
            / f"executor_{executor.id}.log"
        )

        log_handle = open(
            log_path,
            "a",
            encoding="utf-8",
            buffering=1,
        )

        log_handle.write(
            "\n"
            + "=" * 78
            + "\n"
            + f"{datetime.now():%Y-%m-%d %H:%M:%S}"
            + f" | Solicitacao: {sid}\n"
        )

        comando = [
            sys.executable,
            str(SCRIPT_EXECUTOR),
            "--solicitacao-id",
            str(sid),
            "--executor-id",
            str(executor.id),
            "--lock-token",
            token,
        ]

        creationflags = 0

        if os.name == "nt":
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP
            )

        # IMPORTANTE:
        # muda CLAIMED -> RUNNING antes de iniciar o filho.
        # Assim o codigo.py nunca corre o risco de iniciar
        # enquanto a solicitação ainda está CLAIMED.
        if not marcar_running(
            sid,
            executor.id,
            token,
            None,
        ):
            log_handle.close()
            raise RuntimeError(
                "Falha CLAIMED -> RUNNING."
            )

        try:
            processo = subprocess.Popen(
                comando,
                cwd=str(BASE_DIR),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
        except Exception:
            log_handle.close()
            raise

        if not atualizar_pid(
            sid,
            executor.id,
            token,
            processo.pid,
        ):
            self._matar_arvore_processo(
                processo
            )
            log_handle.close()
            raise RuntimeError(
                "Executor iniciou, mas não foi possível "
                "registrar o PID mantendo a posse SQL."
            )

        executor.processo = processo
        executor.solicitacao_id = sid
        executor.lock_token = token
        executor.referencia = (
            solicitacao["referencia_origem"]
            or solicitacao["pedido_cliente"]
            or str(sid)
        )
        executor.etapa = "INICIALIZANDO"
        executor.inicio = time.monotonic()
        executor.ultimo_check_sql = 0.0
        executor.log_handle = log_handle

        self._log(
            f"#{sid} -> EXECUTOR {executor.id} "
            f"(PID {processo.pid})"
        )

    def _verificar_execucoes(self) -> None:
        agora = time.monotonic()

        for executor in self.executores:
            if executor.livre:
                continue

            processo = executor.processo

            if (
                processo is None
                or executor.solicitacao_id is None
                or executor.lock_token is None
            ):
                continue

            retorno = processo.poll()

            if retorno is not None:
                self._processo_finalizado(
                    executor,
                    retorno,
                )
                continue

            if (
                agora - executor.ultimo_check_sql
                < INTERVALO_STATUS_SQL_SECONDS
            ):
                continue

            executor.ultimo_check_sql = agora

            info = obter_execucao(
                executor.solicitacao_id,
                executor.id,
                executor.lock_token,
            )

            if info is None:
                self._log(
                    f"POSSE PERDIDA #{executor.solicitacao_id}. "
                    "Matando executor."
                )
                self._matar_arvore_processo(
                    processo
                )
                self._liberar(
                    executor
                )
                continue

            executor.etapa = (
                info.get("ultima_etapa")
                or executor.etapa
            )

            idade = info.get(
                "heartbeat_age_seconds"
            )

            if (
                idade is not None
                and idade
                > ORCHESTRATOR_STALE_HEARTBEAT_SECONDS
            ):
                sid = executor.solicitacao_id

                self._log(
                    f"HEARTBEAT TIMEOUT #{sid}: "
                    f"{idade}s sem atualização."
                )

                # Segurança: mata primeiro.
                self._matar_arvore_processo(
                    processo
                )

                # Só depois libera status.
                finalizar_timeout(
                    sid,
                    executor.id,
                    executor.lock_token,
                    (
                        f"Heartbeat parado por {idade}s. "
                        "Árvore do processo encerrada."
                    ),
                )

                self._liberar(
                    executor
                )

    def _processo_finalizado(
        self,
        executor: Executor,
        retorno: int,
    ) -> None:
        sid = executor.solicitacao_id

        if (
            sid is None
            or executor.lock_token is None
        ):
            self._liberar(executor)
            return

        info = obter_execucao(
            sid,
            executor.id,
            executor.lock_token,
        )

        if info is None:
            self._log(
                f"Executor {executor.id} terminou "
                f"sem registro SQL para #{sid}."
            )
            self._liberar(executor)
            return

        status = info.get("status")

        if status == "SUCCESS":
            self._log(
                f"SUCCESS #{sid} | OV "
                f"{info.get('sap_ov')}"
            )

        elif status in {
            "ERROR",
            "TIMEOUT",
            "CANCELLED",
        }:
            self._log(
                f"{status} #{sid}: "
                f"{info.get('ultimo_erro') or ''}"
            )

        else:
            erro = (
                f"Executor encerrou com código {retorno} "
                f"mas SQL permaneceu em {status}."
            )

            finalizar_erro_orquestrador(
                sid,
                executor.id,
                executor.lock_token,
                erro,
            )
            self._log(
                f"ERROR #{sid}: {erro}"
            )

        self._liberar(executor)

    def _liberar(
        self,
        executor: Executor,
    ) -> None:
        if executor.log_handle is not None:
            try:
                executor.log_handle.close()
            except Exception:
                pass

        executor.processo = None
        executor.solicitacao_id = None
        executor.lock_token = None
        executor.referencia = None
        executor.etapa = None
        executor.inicio = None
        executor.ultimo_check_sql = 0.0
        executor.log_handle = None
        executor.livre_desde = time.monotonic()

    def _atualizar_cache(self) -> None:
        self.pendentes_cache = (
            listar_pendentes()
        )
        self.contagens_cache = contagens()

    def _atualizar_tela(self) -> None:
        self.lbl_fila.config(
            text=(
                "Pendentes: "
                f"{self.contagens_cache.get('PENDING', 0)}"
            )
        )

        self.lbl_success.config(
            text=(
                "SUCCESS: "
                f"{self.contagens_cache.get('SUCCESS', 0)}"
            )
        )

        self.listbox_fila.delete(
            0,
            tk.END,
        )

        for item in self.pendentes_cache:
            ref = (
                item["referencia_origem"]
                or item["pedido_cliente"]
                or "—"
            )

            self.listbox_fila.insert(
                tk.END,
                (
                    f"#{item['solicitacao_id']} | "
                    f"{item['origem']} | {ref}"
                ),
            )

        agora = time.monotonic()

        for executor in self.executores:
            card = self.cards[
                executor.id
            ]

            if executor.livre:
                card["status"].config(
                    text="LIVRE",
                    bg="#dff3e7",
                    fg="#116329",
                )
                card["solicitacao"].config(
                    text="Solicitação: —"
                )
                card["etapa"].config(
                    text="Etapa: —"
                )
                card["tempo"].config(
                    text="Tempo: —"
                )
                card["pid"].config(
                    text="PID: —"
                )
                continue

            card["status"].config(
                text="EXECUTANDO",
                bg="#dceef8",
                fg="#005b85",
            )
            card["solicitacao"].config(
                text=(
                    f"Solicitação: "
                    f"#{executor.solicitacao_id} "
                    f"({executor.referencia or '—'})"
                )
            )
            card["etapa"].config(
                text=(
                    f"Etapa: "
                    f"{executor.etapa or '—'}"
                )
            )

            decorrido = 0

            if executor.inicio is not None:
                decorrido = int(
                    agora - executor.inicio
                )

            minutos, segundos = divmod(
                decorrido,
                60,
            )
            card["tempo"].config(
                text=(
                    f"Tempo: {minutos:02d}:"
                    f"{segundos:02d}"
                )
            )
            card["pid"].config(
                text=(
                    f"PID: "
                    f"{executor.processo.pid}"
                )
            )

    def _log(self, mensagem: str) -> None:
        horario = datetime.now().strftime(
            "%H:%M:%S"
        )
        linha = (
            f"[{horario}] {mensagem}\n"
        )

        self.txt_log.config(
            state="normal"
        )
        self.txt_log.insert(
            tk.END,
            linha,
        )
        self.txt_log.see(
            tk.END
        )
        self.txt_log.config(
            state="disabled"
        )

    def _limpar_eventos(self) -> None:
        """
        Limpa somente o console visual.

        Não apaga os arquivos de log dos executores,
        não altera o SQL e não altera histórico/status.
        """
        if self.txt_log is None:
            return

        self.txt_log.config(
            state="normal"
        )
        self.txt_log.delete(
            "1.0",
            tk.END,
        )
        self.txt_log.config(
            state="disabled"
        )

    def _alternar_pausa(self) -> None:
        self.pausado = not self.pausado

        if self.pausado:
            self.btn_pausa.config(
                text="Retomar alocações"
            )
            self._log(
                "Novas alocações PAUSADAS."
            )
        else:
            self.btn_pausa.config(
                text="Pausar novas alocações"
            )
            self._log(
                "Novas alocações RETOMADAS."
            )

    @staticmethod
    def _matar_arvore_processo(
        processo: subprocess.Popen,
    ) -> None:
        if processo.poll() is not None:
            return

        try:
            if os.name == "nt":
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(processo.pid),
                        "/T",
                        "/F",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            else:
                processo.kill()

        except Exception:
            try:
                processo.kill()
            except Exception:
                pass

    def _ao_fechar(self) -> None:
        executando = [
            e
            for e in self.executores
            if not e.livre
        ]

        if executando:
            confirmar = messagebox.askyesno(
                "Encerrar orquestrador",
                "Existem executores rodando.\n\n"
                "Deseja encerrar o orquestrador "
                "e finalizar esses processos?",
            )

            if not confirmar:
                return

        self.encerrando = True

        for executor in executando:
            try:
                if executor.processo is not None:
                    self._matar_arvore_processo(
                        executor.processo
                    )

                if (
                    executor.solicitacao_id
                    is not None
                    and executor.lock_token
                    is not None
                ):
                    finalizar_erro_orquestrador(
                        executor.solicitacao_id,
                        executor.id,
                        executor.lock_token,
                        (
                            "Orquestrador encerrado "
                            "manualmente."
                        ),
                    )
            except Exception:
                pass

            if executor.log_handle is not None:
                try:
                    executor.log_handle.close()
                except Exception:
                    pass

        self.root.destroy()


if __name__ == "__main__":
    if not SCRIPT_EXECUTOR.exists():
        raise FileNotFoundError(
            f"Não encontrei {SCRIPT_EXECUTOR}"
        )

    root = tk.Tk()
    app = OrquestradorApp(root)
    root.mainloop()