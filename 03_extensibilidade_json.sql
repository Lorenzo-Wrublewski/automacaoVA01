USE [DB_DCLA_DATA_LAKE_SQL];
GO

/*
Opcional, mas recomendado para transformar o template em um
executor VA01 realmente coringa sem criar dezenas de colunas
a cada novo campo mapeado.

Os campos atuais continuam funcionando normalmente.
*/

IF COL_LENGTH('dbo.VA01_Solicitacao', 'parametros_json') IS NULL
BEGIN
    ALTER TABLE dbo.VA01_Solicitacao
        ADD parametros_json NVARCHAR(MAX) NULL;
END;
GO

IF COL_LENGTH('dbo.VA01_Item', 'parametros_json') IS NULL
BEGIN
    ALTER TABLE dbo.VA01_Item
        ADD parametros_json NVARCHAR(MAX) NULL;
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.check_constraints
    WHERE name = 'CK_VA01_Solicitacao_parametros_json'
)
BEGIN
    ALTER TABLE dbo.VA01_Solicitacao
        ADD CONSTRAINT CK_VA01_Solicitacao_parametros_json
        CHECK (
            parametros_json IS NULL
            OR ISJSON(parametros_json) = 1
        );
END;
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.check_constraints
    WHERE name = 'CK_VA01_Item_parametros_json'
)
BEGIN
    ALTER TABLE dbo.VA01_Item
        ADD CONSTRAINT CK_VA01_Item_parametros_json
        CHECK (
            parametros_json IS NULL
            OR ISJSON(parametros_json) = 1
        );
END;
GO
