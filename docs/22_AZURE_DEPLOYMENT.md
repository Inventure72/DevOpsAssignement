# Azure deployment

The [system manifest](../system.yaml) names the course resource group,
Container Apps environment, SQL server and databases, and the Azure Storage
static website. The configuration is scoped to the `mgiorgetti-ieu2024` prefix
in `rg-sdd-3a`. The storage account uses the same prefix without punctuation.

## First setup

1. Store the professor's class service-principal JSON in the repository's
   GitHub Actions secret `AZURE_CREDENTIALS`.
2. Set `AZURE_SQL_ADMIN_PASSWORD`, `ROOMS_SQL_PASSWORD`, and
   `GAME_SQL_PASSWORD` to distinct strong passwords. Set
   `REPEAT_SERVICE_TOKEN` to a separate random value. Container Apps receive
   SQL URLs and service credentials as secret references.
3. For private GHCR images, set `GHCR_PULL_TOKEN` to an Inventure72 classic
   token with `read:packages` and access to both images. Azure needs pull
   access after scaling to zero. Public GHCR packages can be pulled without
   a registry credential.
4. Push changes through a pull request and merge after the checks pass.
   The [workflow](../.github/workflows/ci.yml) runs every CI job on the main
   commit before invoking [deployment](../.github/workflows/deploy.yml).

The first deployment uses [the foundation template](../deploy/azure-foundation.bicep)
to create the prefixed environment, Log Analytics workspace, storage account
and SQL resources. The deployment script initializes the two scoped SQL users,
applies [the apps template](../deploy/azure-apps.bicep) and verifies that each
`/health` returns its own database readiness and the deployed SHA. It generates
`config.js` with the two public HTTPS API origins and uploads `ui/` to the
storage account's `$web` container. The deployed UI uses its storage endpoint
as the exact CORS origin.

The pipeline uses incremental infrastructure deployment. It keeps the
databases and existing data. The SQL users are created only when missing.
Changing a password in GitHub alone leaves an existing SQL user unchanged,
so rotate the database credential and its GitHub secret together.

## Baseline evidence

After the first successful deployment, play a five-round Demo at the
manifest's public URL. Record the workflow run, deployed SHA, match ID and
final scores before adding the migration. These records are the comparison
point for the next deployment. Search the retained Log Analytics workspace
for the startup migration lines and route-level request logs.

References: [Container Apps scaling](https://learn.microsoft.com/en-us/azure/container-apps/scale-app),
[SQL Database serverless tier](https://learn.microsoft.com/en-us/azure/azure-sql/database/serverless-tier-overview),
[Storage static websites](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-static-website).
