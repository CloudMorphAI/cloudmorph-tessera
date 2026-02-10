# Getting Started with CloudMorph Control Centre

## Overview

CloudMorph Control Centre lets you govern AI agents (Codex, Cursor, custom) 
across AWS, GCP, Azure, Databricks, and Snowflake with policy-driven access 
control.

## Quick Start (5 minutes)

### 1. Sign Up

Visit [app.cloudmorph.io](https://app.cloudmorph.io) and create a free account.
The free tier includes:

- 2 integrations (tokens)
- 1 cloud account binding
- 100 requests/day
- 1 concurrent job

### 2. Create an Integration Token

1. Go to **Dashboard > Access**
2. Enter your email and select an integration app (Codex or Cursor)
3. Select your cloud provider and account
4. Assign a permission pack (Read-First recommended for starters)
5. Click **Mint Token**
6. Copy the token — it's shown only once!

### 3. Install the SDK

**TypeScript/JavaScript:**
```bash
npm install @cloudmorph/sdk
```

**Python:**
```bash
pip install cloudmorph
```

### 4. Make Your First Request

**TypeScript:**
```typescript
import { CloudMorphClient } from "@cloudmorph/sdk";

const client = new CloudMorphClient({ token: "cm_your_token" });

const result = await client.requestAndWait("aws.s3.list_buckets");
console.log(result.output);
```

**Python:**
```python
from cloudmorph import CloudMorph

cm = CloudMorph(token="cm_your_token")
result = cm.request_and_wait("aws.s3.list_buckets")
print(result["output"])
```

### 5. Connect to Codex or Cursor

**For Cursor**, add to your `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "cloudmorph": {
      "url": "https://mcp.cloudmorph.io/mcp",
      "headers": {
        "Authorization": "Bearer cm_your_token"
      }
    }
  }
}
```

**For Codex**, set the environment variable:
```bash
export CLOUDMORPH_TOKEN=cm_your_token
```

## Supported Operations

### AWS
| Action | Description |
|--------|-------------|
| `aws.s3.list_buckets` | List all S3 buckets |
| `aws.s3.list_objects` | List objects in a bucket |
| `aws.ec2.list_instances` | List EC2 instances (multi-region) |
| `aws.ecs.list_clusters` | List ECS clusters |
| `aws.ecs.list_services` | List ECS services |
| `aws.ecs.list_tasks` | List ECS tasks |

### GCP
| Action | Description |
|--------|-------------|
| `gcp.storage.list_buckets` | List GCS buckets |
| `gcp.storage.list_objects` | List objects in a bucket |
| `gcp.compute.list_instances` | List Compute Engine VMs |
| `gcp.run.list_jobs` | List Cloud Run jobs |
| `gcp.run.list_services` | List Cloud Run services |
| `gcp.container.list_clusters` | List GKE clusters |

### Azure
| Action | Description |
|--------|-------------|
| `azure.blob.list_containers` | List storage containers |
| `azure.blob.list_blobs` | List blobs in a container |
| `azure.compute.list_vms` | List virtual machines |
| `azure.containerapps.list_apps` | List Container Apps |
| `azure.containerapps.list_jobs` | List Container App jobs |

### Databricks (Preview)
| Action | Description |
|--------|-------------|
| `databricks.workspace.list_clusters` | List workspace clusters |
| `databricks.workspace.list_jobs` | List workspace jobs |
| `databricks.workspace.list_notebooks` | List notebooks |
| `databricks.sql.list_warehouses` | List SQL warehouses |
| `databricks.unity_catalog.list_catalogs` | List Unity Catalog catalogs |
| `databricks.unity_catalog.list_schemas` | List schemas in a catalog |

### Snowflake (Preview)
| Action | Description |
|--------|-------------|
| `snowflake.account.list_databases` | List databases |
| `snowflake.account.list_warehouses` | List warehouses |
| `snowflake.database.list_schemas` | List schemas in a database |
| `snowflake.schema.list_tables` | List tables in a schema |
| `snowflake.account.list_roles` | List roles |

## Permission Packs

| Pack | Behavior |
|------|----------|
| **Deny-First** | Block all requests by default; only allow explicitly listed read actions |
| **Read-First** | Allow read-only actions (list/describe/get); block destructive writes |

## Architecture

```
Agent (Codex/Cursor) → MCP Server → Control Centre API → Policy Engine
                                                              ↓
                                                         Job Queue
                                                              ↓
                                                    Executor (BYOC)
                                                              ↓
                                                     Cloud Provider
```

## Next Steps

- [Link a cloud account](https://app.cloudmorph.io) in the Control Centre UI
- Explore the [TypeScript SDK](../packages/sdk/README.md)
- Explore the [Python SDK](../sdk-python/README.md)
- Review [permission packs](https://docs.cloudmorph.io/packs)
