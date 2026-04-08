# DevLift MCP Server (dummy)

A developer-facing MCP server that lets application developers provision DevLift infrastructure resources through natural language — without needing to know DevOps.

This is the **dummy / stub version**: it captures the form fields, validates that required ones are present, fills defaults for omitted optional fields, and returns an acknowledgment as if the resource had been created. **No real cloud resources are provisioned.**

## How it works

A developer says something like:

> "I need an S3 bucket for storing service files."

The MCP client (Claude Desktop, Claude Code, etc.) walks them through:

1. **`list_supported_resources`** — discover what's available
2. **`describe_resource("s3_bucket")`** — find out what fields the user must provide
3. The client asks the user for each required field
4. **`provision_resource(resource_type, attributes, product, environment, geo_location)`** — submit, get an acknowledgment back

## Supported resource types

| Type | Description |
|---|---|
| `s3_bucket` | Object storage bucket for files, backups, static assets |
| `sqs_queue` | Managed message queue for asynchronous processing |
| `dynamodb_table` | Serverless NoSQL key/value store |
| `database` | Relational database on a managed DB server |
| `eks_service` | Containerized application hosted on EKS (Kubernetes) |
| `ecs_service` | Containerized application hosted on ECS Fargate |

Field naming follows the chat-bot-POC vance form metadata so the schemas line up with what the broader DevLift platform already uses.

## Common placement fields

Every resource needs three placement fields in addition to its own attributes:

- `product` — `core` | `falcon`
- `environment` — `stage` | `prod`
- `geo_location` — `Mumbai` | `London` | `Canada`

## Tools exposed

- **`list_supported_resources()`** — returns the catalog of resource types and the supported placement values.
- **`describe_resource(resource_type)`** — returns required fields, optional fields with defaults, and the placement fields the LLM needs to ask the user about.
- **`provision_resource(resource_type, attributes, product, environment, geo_location)`** — validates the form, fills defaults, returns `{status: "acknowledged", resource_id, ...}` on success or `{status: "incomplete", missing_required_fields: [...]}` if anything is missing.

## Run

```bash
pip install -r requirements.txt
python server.py
```

This starts a stdio MCP server. Connect it to your MCP client (Claude Desktop, Claude Code, etc.).

## Status

**Dummy.** No cloud calls. Returns `status: "acknowledged"` with a fake `resource_id`. Adding a new resource type = adding one entry to `RESOURCE_CATALOG` in [server.py](server.py).
