# Azure OpenAI Image Generation – Setup Guide

This document explains how to configure the repository so the
**Generate Image with Azure OpenAI** workflow can authenticate to Azure and
call Azure OpenAI to produce images.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| GitHub repository with Actions enabled | This repository |
| Azure subscription | Any tier |
| Azure OpenAI resource | Must be in a region that supports image generation |
| Image model deployment | E.g. a `gpt-image-2` deployment |
| Microsoft Entra application or managed identity | Used by GitHub Actions to authenticate |
| `Azure AI User` role assignment | Assign to the identity on the Azure OpenAI resource |

---

## Recommended authentication: GitHub Actions OIDC

The workflow uses **OpenID Connect (OIDC)** to exchange a short-lived GitHub
Actions token for an Azure access token.  No long-lived secret is stored in
GitHub.

### 1. Create or choose a Microsoft Entra application

In the [Azure portal](https://portal.azure.com) or with the Azure CLI:

```bash
az ad app create --display-name "github-image-generator"
az ad sp create --id <appId>
```

Record the **Application (client) ID** and the **Directory (tenant) ID**.

### 2. Assign the `Azure AI User` role

```bash
az role assignment create \
  --assignee <clientId> \
  --role "Azure AI User" \
  --scope /subscriptions/<subscriptionId>/resourceGroups/<rg>/providers/Microsoft.CognitiveServices/accounts/<aoaiResource>
```

### 3. Add a federated credential

Add a federated credential on the app registration so Azure trusts tokens
issued for this repository.

For a **branch-based** workflow (triggers on `main`):

```text
Subject: repo:OWNER/REPO:ref:refs/heads/main
```

For a **GitHub Environment** workflow:

```text
Subject: repo:OWNER/REPO:environment:ENVIRONMENT_NAME
```

Via the CLI:

```bash
az ad app federated-credential create \
  --id <appId> \
  --parameters '{
    "name": "github-main",
    "issuer": "https://token.actions.githubusercontent.com",
    "subject": "repo:OWNER/REPO:ref:refs/heads/main",
    "audiences": ["api://AzureADTokenExchange"]
  }'
```

---

## GitHub repository configuration

Add these as **repository variables** or **repository secrets** (Settings →
Secrets and variables → Actions). The workflow checks variables first and
falls back to secrets with the same names.

| Name | Example value |
|---|---|
| `AZURE_CLIENT_ID` | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `AZURE_TENANT_ID` | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `AZURE_SUBSCRIPTION_ID` | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| `AZURE_OPENAI_ENDPOINT` | `https://my-resource.openai.azure.com/` |
| `AZURE_OPENAI_IMAGE_DEPLOYMENT` | E.g. `gpt-image-2` (deployment name, NOT model name) |

### Optional API-key fallback

If OIDC is unavailable and the Azure OpenAI resource has API key
authentication enabled, add this as a **repository secret**:

| Secret | Notes |
|---|---|
| `AZURE_OPENAI_API_KEY` | Only used when the variable is present and non-empty |

> ⚠️ API key fallback is a weaker security posture.  Prefer OIDC.

---

## Running the workflow

1. Go to **Actions** in the repository.
2. Select **Generate Image with Azure OpenAI**.
3. Click **Run workflow**.
4. Fill in the inputs:

   | Input | Description | Default |
   |---|---|---|
   | `prompt` | Text description of the desired image | *(required)* |
   | `size` | Image dimensions | `1024x1024` |
   | `quality` | `standard` or `hd` | `standard` |
   | `style` | `vivid` or `natural` | `vivid` |
   | `output_filename` | File name for the saved image | `image.png` |

5. Click **Run workflow** to start.
6. When the run completes, download the **generated-image** artifact which
   contains the image file and a JSON metadata sidecar.

---

## File layout

```
.github/
  workflows/
    generate-image.yml   GitHub Actions workflow
scripts/
  generate-image.py      Python image generation script
requirements.txt         Python dependencies
docs/
  image-generation-setup.md  This file
```

---

## Troubleshooting

### `DefaultAzureCredential failed`

- Confirm the workflow has `id-token: write` permission (already present).
- Confirm `azure/login@v2` ran successfully in the same job.
- Check the federated credential subject matches the repository and branch or
  environment.

### `401` or `403`

- Verify `AZURE_OPENAI_ENDPOINT`, `AZURE_CLIENT_ID`, and `AZURE_TENANT_ID`.
- Confirm the identity has the `Azure AI User` role on the Azure OpenAI
  resource (not just the subscription).
- Check the federated credential is on the correct Entra app and the subject
  is exact.

### `404 deployment not found`

- `AZURE_OPENAI_IMAGE_DEPLOYMENT` must be the **deployment name** you chose
  when deploying the model, not the underlying model ID.

### `API key authentication disabled`

- Remove `AZURE_OPENAI_API_KEY` from secrets and use OIDC with Azure RBAC.

### `429 Too Many Requests`

- Reduce request frequency or add retry logic with exponential back-off.

### `content_filter`

- Revise the prompt to comply with
  [Azure OpenAI content policy](https://learn.microsoft.com/azure/ai-services/openai/concepts/content-filter).

---

## Security notes

- **Prefer OIDC** over long-lived API keys.  OIDC tokens are short-lived and
  scoped to a single workflow run.
- **Never** include API keys, tokens, or client secrets in issue text, pull
  request descriptions, workflow logs, or committed files.
- Use **GitHub Environments** with required reviewers if you need manual
  approval before the workflow runs.
- Scope Azure role assignments as narrowly as practical (resource-level, not
  subscription-level).
- Generated image URLs returned by the Azure OpenAI API are **temporary**.
  The workflow saves the image bytes to a file and uploads it as a durable
  artifact; the URL itself is not logged.
