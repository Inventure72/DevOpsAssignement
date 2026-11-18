-- Local SQL Server bootstrap, executed by pymssql with a bound rooms_password.
-- Azure database/login provisioning is separate and must use the student's prefix.
IF DB_ID('repeat_rooms') IS NULL
    EXEC('CREATE DATABASE repeat_rooms');
GO
DECLARE @password NVARCHAR(128) = %(rooms_password)s;
IF SUSER_ID('repeat_rooms_user') IS NULL
BEGIN
    DECLARE @sql NVARCHAR(MAX) = N'CREATE LOGIN repeat_rooms_user WITH PASSWORD='
        + QUOTENAME(@password, '''') + N', CHECK_POLICY=ON, DEFAULT_DATABASE=repeat_rooms';
    EXEC sys.sp_executesql @sql;
END;
GO
USE repeat_rooms;
IF USER_ID('repeat_rooms_user') IS NULL
    CREATE USER repeat_rooms_user FOR LOGIN repeat_rooms_user WITH DEFAULT_SCHEMA=dbo;
IF IS_ROLEMEMBER('db_datareader','repeat_rooms_user')<>1
    ALTER ROLE db_datareader ADD MEMBER repeat_rooms_user;
IF IS_ROLEMEMBER('db_datawriter','repeat_rooms_user')<>1
    ALTER ROLE db_datawriter ADD MEMBER repeat_rooms_user;
IF IS_ROLEMEMBER('db_ddladmin','repeat_rooms_user')<>1
    ALTER ROLE db_ddladmin ADD MEMBER repeat_rooms_user;
