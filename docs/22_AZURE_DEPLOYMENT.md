# Azure deployment

The [system manifest](../system.yaml) names the student resource group,
Container Apps environment, SQL server and databases, and the Azure Storage
static website. The configuration is scoped to the `mgiorgetti-ieu2024` prefix
in `rg-mgiorgetti-ieu2024` on the student's Azure for Students subscription
in the IE University directory.

## First setup

1. Select the IE University directory and the student's **Azure for Students**
   subscription. Create `rg-mgiorgetti-ieu2024` in **France Central**, a
   region allowed by the subscription policy. Create the user-assigned managed
   identity `mgiorgetti-ieu2024-github` and grant it **Contributor** on this
   resource group only. Keep the Log Analytics workspace created by the
   deployment.
2. Add the two GitHub federated credentials to that identity. For each, set
   issuer `https://token.actions.githubusercontent.com` and audience
   `api://AzureADTokenExchange`. Use the `main` branch in both subjects.

   - Named form `repo:Inventure72/DevOpsAssignement:ref:refs/heads/main`
   - Immutable form `repo:Inventure72@72494613/DevOpsAssignement@1390813610:ref:refs/heads/main`

   Verify both IDs with `gh api repos/Inventure72/DevOpsAssignement --jq '{owner: .owner.id, repository: .id}'`.
   See [Azure's managed identity
   OIDC setup](https://learn.microsoft.com/en-us/azure/developer/github/connect-from-azure-openid-connect)
   and [GitHub's subject formats](https://docs.github.com/en/actions/reference/security/oidc#immutable-subject-claims).
3. In the repository's **Settings → Secrets and variables → Actions**, create
   GitHub secrets `AZURE_CLIENT_ID` (the identity's client ID),
   `AZURE_TENANT_ID`, and `AZURE_SUBSCRIPTION_ID`. The workflow exchanges a
   short-lived OIDC token on trusted pushes to `main`.
4. Set `AZURE_SQL_ADMIN_PASSWORD`, `ROOMS_SQL_PASSWORD`, and
   `GAME_SQL_PASSWORD` to distinct strong passwords. Set `REPEAT_SERVICE_TOKEN`
   to a separate random value. Container Apps receive SQL URLs and service
   credentials as secret references.
5. Set Actions variables `PREFIX` to `mgiorgetti-ieu2024`, `RESOURCE_GROUP` to
   `rg-mgiorgetti-ieu2024`, and `UI_STORAGE` to the account name in `system.yaml`.
   When the student subscription requires a different SQL location, set
   `AZURE_SQL_LOCATION` to an allowed region with the SQL free offer. The default
   is the resource group's location. Enable the static website in the storage
   account's **Static website** settings and copy its actual HTTPS URL to
   `system.yaml`. This deployment uses `mgiorgettiieu2024ui1`.
6. Push changes through a pull request and merge after the checks pass.
   The [workflow](../.github/workflows/ci.yml) runs every CI job on the main
   commit before invoking [deployment](../.github/workflows/deploy.yml).
   The deployment publishes both `linux/amd64` images tagged with that SHA.
   New GHCR packages start private. If the public pull check fails, open the
   settings for `repeat-rooms` and `repeat-game`, change both package
   visibilities to **Public**, and rerun the failed deployment job on the same
   workflow run. Azure can then pull both images when a replica starts. [GitHub
   Packages visibility settings](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility#configuring-visibility-of-packages-for-your-personal-account).

The first deployment uses [the foundation template](../deploy/azure-foundation.bicep)
to create the prefixed environment, Log Analytics workspace, storage account
and SQL resources. Each database uses General Purpose serverless Gen5 with
0.5–1 vCore and locally redundant backups. The free offer sets the database
size and idle delay. `AutoPause` pauses a database when its monthly free limit
is exhausted. [Azure SQL Database free offer](https://learn.microsoft.com/en-us/azure/azure-sql/database/free-offer).

The deployment script initializes the two scoped SQL users,
applies [the apps template](../deploy/azure-apps.bicep) and verifies that each
`/health` returns its own database readiness and the deployed SHA. It generates
`config.js` with the two public HTTPS API origins and uploads `ui/` to the
storage account's `$web` container. The deployed UI uses its storage endpoint
as the exact CORS origin.

The deployment retrieves the named storage account's key at resource-group
scope and passes it to the static website and upload commands through their
process environment. GitHub Actions needs only the OIDC identity for this step.

The SQL server requires TLS 1.2 and enables the Azure services firewall rule.
For manual SQL administration from outside Azure, add a rule for your current
public IP and remove it when finished. Each application's SQL user is contained
in its own database.

Deployment and both service images build the pinned pymssql package against
FreeTDS 1.5.22. The upstream release fixes OpenSSL's check of Azure SQL wildcard
certificates. The build verifies both source archives. SQL connections still
verify the CA chain and the configured server hostname.

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

The [Container Apps scaling guide](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)
and [Storage static website guide](https://learn.microsoft.com/en-us/azure/storage/blobs/storage-blob-static-website)
cover replica and static hosting behavior.
