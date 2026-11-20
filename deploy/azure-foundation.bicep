targetScope = 'resourceGroup'

param prefix string
param location string = resourceGroup().location
param storageAccountName string = '${replace(prefix, '-', '')}ui'
param sqlAdminLogin string = 'repeat_sql_admin'

@secure()
param sqlAdminPassword string

resource workspace 'Microsoft.OperationalInsights/workspaces@2022-10-01' = {
  name: '${prefix}-logs'
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    workloadProfiles: [
      {
        name: 'Consumption'
        workloadProfileType: 'Consumption'
      }
    ]
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: workspace.properties.customerId
        sharedKey: workspace.listKeys().primarySharedKey
      }
    }
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
  }
}

resource sql 'Microsoft.Sql/servers@2023-08-01' = {
  name: '${prefix}-sql'
  location: location
  properties: {
    administratorLogin: sqlAdminLogin
    administratorLoginPassword: sqlAdminPassword
    version: '12.0'
    minimalTlsVersion: '1.2'
    publicNetworkAccess: 'Enabled'
  }
}

resource azureFirewall 'Microsoft.Sql/servers/firewallRules@2023-08-01' = {
  parent: sql
  name: 'AllowAzureServices'
  properties: {
    startIpAddress: '0.0.0.0'
    endIpAddress: '0.0.0.0'
  }
}

resource databases 'Microsoft.Sql/servers/databases@2023-08-01' = [for service in ['rooms', 'game']: {
  parent: sql
  name: '${prefix}-${service}-db'
  location: location
  sku: {
    name: 'GP_S_Gen5'
    tier: 'GeneralPurpose'
    family: 'Gen5'
    capacity: 1
  }
  properties: {
    minCapacity: json('0.5')
    autoPauseDelay: 60
    maxSizeBytes: 2147483648
    requestedBackupStorageRedundancy: 'Local'
  }
}]

output environmentName string = environment.name
output sqlServerName string = sql.name
output sqlServerFqdn string = sql.properties.fullyQualifiedDomainName
output roomsDatabaseName string = databases[0].name
output gameDatabaseName string = databases[1].name
output uiStorageAccountName string = storage.name
output workspaceName string = workspace.name
