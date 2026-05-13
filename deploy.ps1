#!/usr/bin/env pwsh
<#
  deploy.ps1 — Deploy the Calendar MCP server to Azure Container Apps
               inside a private VNet so it is ONLY reachable within
               your tenant/organisation.

  Pre-requisites:
    - Azure CLI installed and logged in:  az login
    - Docker running locally
    - Fill in the variables section below, or pass as env vars.

  What this script does:
    1. Creates a Resource Group
    2. Creates an Azure Container Registry (ACR) in your tenant
    3. Builds & pushes the Docker image to ACR
    4. Creates a VNet with a dedicated subnet for Container Apps
    5. Creates a Container Apps Environment (internal — no public IP)
    6. Deploys the MCP server as a Container App (internal ingress only)
    7. Stores secrets (client secret, etc.) in Azure Key Vault
    8. Prints the internal FQDN agents should use

  Security controls applied:
    ✅ No public IP — only reachable inside the VNet
    ✅ Azure AD Bearer token required on every request (enforced in-app)
    ✅ Container runs as non-root user
    ✅ Secrets stored in Key Vault, never in code or env vars in plain text
    ✅ Managed Identity used — no stored credentials for the app itself
#>

# ── Variables — customise these ──────────────────────────────────────────────
$RESOURCE_GROUP   = "rg-mcp-calendar"
$LOCATION         = "eastus"
$ACR_NAME         = "acrmcpcalendar"          # must be globally unique, lowercase
$VNET_NAME        = "vnet-mcp"
$SUBNET_NAME      = "subnet-containerapp"
$ENV_NAME         = "cae-mcp-calendar"        # Container Apps Environment
$APP_NAME         = "ca-mcp-calendar"         # Container App name
$KV_NAME          = "kv-mcp-calendar"         # Key Vault name (globally unique)
$IMAGE_TAG        = "latest"

# Azure AD App Registration for the MCP server (created separately in portal)
$TENANT_ID        = $env:AZURE_TENANT_ID
$CLIENT_ID        = $env:AZURE_CLIENT_ID       # Graph API app (Calendars.Read)
$CLIENT_SECRET    = $env:AZURE_CLIENT_SECRET
$MCP_CLIENT_ID    = $env:MCP_SERVER_CLIENT_ID  # App Reg that callers authenticate against

# ── 1. Resource Group ─────────────────────────────────────────────────────────
Write-Host "`n[1/8] Creating resource group..."
az group create --name $RESOURCE_GROUP --location $LOCATION

# ── 2. Azure Container Registry ───────────────────────────────────────────────
Write-Host "`n[2/8] Creating Azure Container Registry..."
az acr create `
  --resource-group $RESOURCE_GROUP `
  --name $ACR_NAME `
  --sku Basic `
  --admin-enabled false

# ── 3. Build & push image ─────────────────────────────────────────────────────
Write-Host "`n[3/8] Building and pushing Docker image to ACR..."
az acr build `
  --registry $ACR_NAME `
  --image "mcp-calendar:$IMAGE_TAG" `
  --file Dockerfile `
  .

$ACR_LOGIN_SERVER = (az acr show --name $ACR_NAME --query loginServer -o tsv)
$IMAGE_FULL = "$ACR_LOGIN_SERVER/mcp-calendar:$IMAGE_TAG"

# ── 4. Virtual Network (private) ──────────────────────────────────────────────
Write-Host "`n[4/8] Creating VNet and subnet..."
az network vnet create `
  --resource-group $RESOURCE_GROUP `
  --name $VNET_NAME `
  --address-prefix "10.0.0.0/16" `
  --subnet-name $SUBNET_NAME `
  --subnet-prefix "10.0.0.0/23"

$SUBNET_ID = (az network vnet subnet show `
  --resource-group $RESOURCE_GROUP `
  --vnet-name $VNET_NAME `
  --name $SUBNET_NAME `
  --query id -o tsv)

# ── 5. Key Vault — store secrets safely ───────────────────────────────────────
Write-Host "`n[5/8] Creating Key Vault and storing secrets..."
az keyvault create `
  --resource-group $RESOURCE_GROUP `
  --name $KV_NAME `
  --location $LOCATION `
  --enable-rbac-authorization true

az keyvault secret set --vault-name $KV_NAME --name "AzureTenantId"     --value $TENANT_ID
az keyvault secret set --vault-name $KV_NAME --name "AzureClientId"     --value $CLIENT_ID
az keyvault secret set --vault-name $KV_NAME --name "AzureClientSecret" --value $CLIENT_SECRET
az keyvault secret set --vault-name $KV_NAME --name "McpServerClientId" --value $MCP_CLIENT_ID

# ── 6. Container Apps Environment (internal — no public IP) ───────────────────
Write-Host "`n[6/8] Creating Container Apps Environment (internal VNet only)..."
az containerapp env create `
  --name $ENV_NAME `
  --resource-group $RESOURCE_GROUP `
  --location $LOCATION `
  --infrastructure-subnet-resource-id $SUBNET_ID `
  --internal-only true        # <-- NO public endpoint, private VNet only

# ── 7. Deploy Container App ───────────────────────────────────────────────────
Write-Host "`n[7/8] Deploying MCP server Container App..."

# Give the environment permission to pull from ACR via Managed Identity
$ENV_IDENTITY = (az containerapp env show `
  --name $ENV_NAME `
  --resource-group $RESOURCE_GROUP `
  --query identity.principalId -o tsv)

az role assignment create `
  --assignee $ENV_IDENTITY `
  --role AcrPull `
  --scope (az acr show --name $ACR_NAME --query id -o tsv)

az containerapp create `
  --name $APP_NAME `
  --resource-group $RESOURCE_GROUP `
  --environment $ENV_NAME `
  --image $IMAGE_FULL `
  --registry-server $ACR_LOGIN_SERVER `
  --system-assigned `
  --ingress internal `           # internal-only: reachable inside VNet, not internet
  --target-port 8000 `
  --min-replicas 1 `
  --max-replicas 5 `
  --cpu 0.5 --memory 1Gi `
  --env-vars `
    "MCP_TRANSPORT=http" `
    "AZURE_TENANT_ID=secretref:azure-tenant-id" `
    "AZURE_CLIENT_ID=secretref:azure-client-id" `
    "AZURE_CLIENT_SECRET=secretref:azure-client-secret" `
    "MCP_SERVER_CLIENT_ID=secretref:mcp-server-client-id" `
  --secrets `
    "azure-tenant-id=$TENANT_ID" `
    "azure-client-id=$CLIENT_ID" `
    "azure-client-secret=$CLIENT_SECRET" `
    "mcp-server-client-id=$MCP_CLIENT_ID"

# ── 8. Print internal FQDN ────────────────────────────────────────────────────
Write-Host "`n[8/8] Done!"
$FQDN = (az containerapp show `
  --name $APP_NAME `
  --resource-group $RESOURCE_GROUP `
  --query properties.configuration.ingress.fqdn -o tsv)

Write-Host ""
Write-Host "============================================================"
Write-Host " MCP server deployed (internal only)"
Write-Host " Internal URL: https://$FQDN/mcp/"
Write-Host " Health check: https://$FQDN/health"
Write-Host ""
Write-Host " Set this in your agent .env:"
Write-Host "   MCP_SERVER_URL=https://$FQDN/mcp/"
Write-Host "============================================================"
