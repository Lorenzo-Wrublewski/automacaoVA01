class SapError(RuntimeError):
    pass


class SapLoadingTimeout(SapError):
    pass


class SapStateError(SapError):
    pass


class SapMessageError(SapError):
    pass


class PularItem(SapError):
    pass


class AbortarSolicitacao(SapError):
    pass


class MapeamentoPendente(SapError):
    pass
