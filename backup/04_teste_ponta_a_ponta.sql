USE [DB_DCLA_DATA_LAKE_SQL];
GO

/*
TESTE PONTA A PONTA DO EXECUTOR VA01.

ATENÇÃO:
- Este registro entra como PENDING e será pego pelo orquestrador.
- O ambiente SAP configurado deve ser o QRA usado nos testes.
- O Purchase Order é gerado com timestamp para não duplicar.
*/

DECLARE @solicitacao_id BIGINT;
DECLARE @pedido_cliente NVARCHAR(100);

SET @pedido_cliente =
    CONCAT(
        'BOT-VA01-',
        FORMAT(GETDATE(), 'yyyyMMdd-HHmmss')
    );

INSERT INTO dbo.VA01_Solicitacao
(
    origem,
    referencia_origem,
    chave_idempotencia,

    tipo_ordem,
    organizacao_vendas,
    canal_distribuicao,
    setor_atividade,

    cliente,
    pedido_cliente,
    data_pedido,

    transportadora,
    redespacho,

    status,
    prioridade
)
VALUES
(
    'TESTE',
    @pedido_cliente,
    CONCAT('TESTE:', NEWID()),

    'YSTD',
    'BR10',
    '50',
    '00',

    '6751799',
    @pedido_cliente,
    CAST(GETDATE() AS DATE),

    '98038533',
    '98038549',

    'PENDING',
    100
);

SET @solicitacao_id = SCOPE_IDENTITY();

INSERT INTO dbo.VA01_Item
(
    solicitacao_id,
    sequencia,
    linha_origem,
    material,
    quantidade,
    unidade,
    data_desejada
)
VALUES
(
    @solicitacao_id,
    1,
    '10',
    'R902413332',
    1,
    'PC',
    CAST(GETDATE() AS DATE)
);

SELECT
    solicitacao_id,
    origem,
    referencia_origem,
    cliente,
    pedido_cliente,
    transportadora,
    redespacho,
    status
FROM dbo.VA01_Solicitacao
WHERE solicitacao_id = @solicitacao_id;

SELECT *
FROM dbo.VA01_Item
WHERE solicitacao_id = @solicitacao_id;
GO
