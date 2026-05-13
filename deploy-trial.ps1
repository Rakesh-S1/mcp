#!/usr/bin/env pwsh
<#
  deploy-trial.ps1 — Simplified deployment for Azure free trial / demos.

  Differences from deploy.ps1 (production):
    - No VNet / private subnet  (free trial has quota limits)
    - External ingress          (so you can test /health from browser/curl)
    - Secrets as env vars       (no Key Vault needed)
    - Azure AD auth still fully enforced inside the app

  Run from Azure Cloud Shell:
    ./deploy-trial.ps1
#>

# ── Variables ────────────────────────────────────────────────────────────────
$RESOURCE_GROUP = "rg-mcp-calendar"
$LOCATION       = "eastus"
$ACR_NAME       = "acrmcpcal$(Get-Random -Maximum 9999)"   # unique name
$ENV_NAME       = "cae-mcp-trial"
$APP_NAME       = "ca-mcp-calendar"
$IMAGE_TAG      = "latest"

# Read from env vars set in Cloud Shell
$TENANT_ID       = $env:AZURE_TENANT_ID
$CLIENT_ID       = $env:AZURE_CLIENT_ID
$CLIENT_SECRET   = $env:AZURE_CLIENT_SECRET
$MCP_CLIENT_ID   = $env:MCP_SERVER_CLIENT_ID

# Validate all required vars are present
if (-not $TENANT_ID -or -not $CLIENT_ID -or -not $CLIENT_SECRET -or -not $MCP_CLIENT_ID) {
    Write-Error @"
Missing required environment variables. Set them first:
  `$env:AZURE_TENANT_ID      = '...'
  `$env:AZURE_CLIENT_ID      = '...'
  `$env:AZURE_CLIENT_SECRET  = '...'
  `$env:MCP_SERVER_CLIENT_ID = '...'
"@
    exit 1
}

Write-Host "`n=========================================="
Write-Host " MCP Calendar Server — Trial Deployment"
Write-Host "==========================================`n"

# ── 1. Resource Group ─────────────────────────────────────────────────────────
Write-Host "[1/6] Creating resource group '$RESOURCE_GROUP' in '$LOCATION'..."
az group create --name $RESOURCE_GROUP --location $LOCATION --output none
Write-Host "      Done."

# ── 2. Azure Container Registry ───────────────────────────────────────────────
Write-Host "[2/6] Creating Azure Container Registry '$ACR_NAME'..."
az acr create `
  --resource-group $RESOURCE_GROUP `
  --name $ACR_NAME `
  --sku Basic `
  --admin-enabled true `
  --output none
Write-Host "      Done."

# ── 3. Build & push image via ACR Tasks (no local Docker needed) ──────────────
Write-Host "[3/6] Building Docker image in Azure (no local Docker needed)..."
az acr build `
  --registry $ACR_NAME `
  --image "mcp-calendar:$IMAGE_TAG" `
  --file Dockerfile `
  .
Write-Host "      Image built and pushed."

$ACR_SERVER   = (az acr show --name $ACR_NAME --query loginServer -o tsv)
$ACR_USERNAME = (az acr credential show --name $ACR_NAME --query username -o tsv)
$ACR_PASSWORD = (az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)

# ── 4. Container Apps Environment (no VNet for trial) ─────────────────────────
Write-Host "[4/6] Creating Container Apps Environment..."
az containerapp env create `
  --name $ENV_NAME `
  --resource-group $RESOURCE_GROUP `
  --location $LOCATION `
  --output none
Write-Host "      Done."

# ── 5. Deploy Container App ───────────────────────────────────────────────────
Write-Host "[5/6] Deploying MCP server Container App..."
az containerapp create `
  --name $APP_NAME `
  --resource-group $RESOURCE_GROUP `
  --environment $ENV_NAME `
  --image "$ACR_SERVER/mcp-calendar:$IMAGE_TAG" `
  --registry-server $ACR_SERVER `
  --registry-username $ACR_USERNAME `
  --registry-password $ACR_PASSWORD `
  --ingress external `
  --target-port 8000 `
  --min-replicas 1 `
  --max-replicas 2 `
  --cpu 0.5 --memory 1Gi `
  --env-vars `
    "MCP_TRANSPORT=http" `
    "USE_MOCK=true" `
    "SKIP_AUTH=true" `
    "AZURE_TENANT_ID=$TENANT_ID" `
    "AZURE_CLIENT_ID=$CLIENT_ID" `
    "AZURE_CLIENT_SECRET=secretref:client-secret" `
    "MCP_SERVER_CLIENT_ID=$MCP_CLIENT_ID" `
  --secrets "client-secret=$CLIENT_SECRET" `
  --output none
Write-Host "      Done."

# ── 6. Print results ──────────────────────────────────────────────────────────
$FQDN = (az containerapp show `
  --name $APP_NAME `
  --resource-group $RESOURCE_GROUP `
  --query properties.configuration.ingress.fqdn -o tsv)

Write-Host ""
Write-Host "=========================================="
Write-Host " Deployment complete!"
Write-Host "=========================================="
Write-Host ""
Write-Host " Health check URL:  https://$FQDN/health"
Write-Host " MCP server URL:    https://$FQDN/mcp/"
Write-Host ""
Write-Host " Running with:"
Write-Host "   SKIP_AUTH=true  (no Bearer token needed — trial mode)"
Write-Host "   USE_MOCK=true   (fake calendar data — no O365 needed)"
Write-Host ""
Write-Host " Test health:  curl https://$FQDN/health"
Write-Host " Test MCP:     curl https://$FQDN/mcp/"
Write-Host " Expected MCP response: 200 (no 401 — auth is skipped)"
Write-Host ""
Write-Host " Add to your .env:"
Write-Host "   MCP_SERVER_URL=https://$FQDN/mcp/"
Write-Host "   SKIP_AUTH=true"
Write-Host "=========================================="

# Save FQDN to a file for easy copy-paste
@"
MCP_SERVER_URL=https://$FQDN/mcp/
SKIP_AUTH=true
USE_MOCK=true
"@ | Out-File -FilePath "mcp_server_url.txt" -Encoding UTF8
Write-Host "`n FQDN also saved to: mcp_server_url.txt"
