# DevLift MCP Server (dummy)

A developer-facing MCP server that lets application developers provision DevLift infrastructure resources through natural language — without needing to know DevOps.

This is the **dummy / stub version**: it captures the form fields, validates that required ones are present, fills defaults for omitted optional fields, and returns an acknowledgment as if the resource had been created. **No real cloud resources are provisioned.**

## Architecture

Two pieces, split into a server and a client (mirroring the `ai-workshop` MCP pattern):

- **[server.py](server.py)** — MCP server built on the official `mcp` Python SDK (`mcp.server.fastmcp.FastMCP`). Exposes 3 tools over stdio. No LLM, no business logic — just the resource catalog and form validation.
- **[client.py](client.py)** — MCP client that spawns `server.py` as a subprocess (`stdio_client` + `ClientSession`), discovers tools dynamically, converts MCP tool schemas to OpenAI function-calling format, and runs an agentic chat loop using the OpenAI SDK directly. Pre-fetches `list_supported_resources` once at startup so the LLM has the catalog upfront.

## How it works

A developer says something like:

> "I need an S3 bucket for storing service files."

The client (running an OpenAI LLM under the hood) walks them through:

1. **`list_supported_resources`** — already loaded into the system prompt at startup
2. **`describe_resource("s3_bucket")`** — find out what fields the user must provide
3. The LLM asks the user for each required field in plain language
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
cp .env.example .env
# fill in OPENAI_API_KEY in .env

python client.py
```

`client.py` automatically spawns `server.py` as a stdio subprocess — you don't need to run the server separately.

If you'd rather connect the server to a different MCP client (Claude Desktop, Claude Code, etc.), point it at:

```bash
python server.py
```

### Dependencies

- `mcp` — official MCP Python SDK (server + client)
- `openai` — LLM driving the chat loop
- `python-dotenv` — loads `OPENAI_API_KEY` from `.env`

## Status

**Dummy.** No cloud calls. Returns `status: "acknowledged"` with a fake `resource_id`. Adding a new resource type = adding one entry to `RESOURCE_CATALOG` in [server.py](server.py).
