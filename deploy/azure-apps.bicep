targetScope = 'resourceGroup'

param prefix string
param location string = resourceGroup().location
param uiOrigin string
param roomsImage string
param gameImage string
param gitSha string

@secure()
param roomsDatabaseUrl string

@secure()
param gameDatabaseUrl string

@secure()
param serviceToken string

resource environment 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: '${prefix}-env'
}

var serviceSecrets = [
  {
    name: 'service-token'
    value: serviceToken
  }
]
var environmentVariables = [
  {
    name: 'DATABASE_URL'
    secretRef: 'database-url'
  }
  {
    name: 'SERVICE_TOKEN'
    secretRef: 'service-token'
  }
  {
    name: 'UI_ORIGIN'
    value: uiOrigin
  }
  {
    name: 'GIT_SHA'
    value: gitSha
  }
]
var scale = {
  minReplicas: 0
  maxReplicas: 1
}
var resources = {
  cpu: json('0.25')
  memory: '0.5Gi'
}

func healthProbes(port int) array => [
  {
    type: 'Startup'
    httpGet: {
      path: '/health'
      port: port
    }
    periodSeconds: 5
    timeoutSeconds: 5
    failureThreshold: 48
  }
  {
    type: 'Readiness'
    httpGet: {
      path: '/health'
      port: port
    }
    periodSeconds: 10
    timeoutSeconds: 3
    failureThreshold: 3
  }
]

resource game 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-game'
  location: location
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: []
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8002
        transport: 'auto'
      }
      secrets: concat(serviceSecrets, [
        {
          name: 'database-url'
          value: gameDatabaseUrl
        }
      ])
    }
    template: {
      containers: [
        {
          name: 'game'
          image: gameImage
          resources: resources
          env: concat(environmentVariables, [
            {
              name: 'PORT'
              value: '8002'
            }
          ])
          probes: healthProbes(8002)
        }
      ]
      scale: scale
    }
  }
}

resource rooms 'Microsoft.App/containerApps@2024-03-01' = {
  name: '${prefix}-rooms'
  location: location
  properties: {
    managedEnvironmentId: environment.id
    workloadProfileName: 'Consumption'
    configuration: {
      activeRevisionsMode: 'Single'
      registries: []
      ingress: {
        external: true
        allowInsecure: false
        targetPort: 8001
        transport: 'auto'
      }
      secrets: concat(serviceSecrets, [
        {
          name: 'database-url'
          value: roomsDatabaseUrl
        }
      ])
    }
    template: {
      containers: [
        {
          name: 'rooms'
          image: roomsImage
          resources: resources
          env: concat(environmentVariables, [
            {
              name: 'PORT'
              value: '8001'
            }
            {
              name: 'GAME_BASE_URL'
              value: 'https://${game.properties.configuration!.ingress!.fqdn}'
            }
          ])
          probes: healthProbes(8001)
        }
      ]
      scale: scale
    }
  }
}

output roomsOrigin string = 'https://${rooms.properties.configuration!.ingress!.fqdn}'
output gameOrigin string = 'https://${game.properties.configuration!.ingress!.fqdn}'
