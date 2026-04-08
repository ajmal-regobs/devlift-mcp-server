"""
DevLift MCP Server (dummy)

A developer-facing MCP server that lets application developers provision
infrastructure resources through a conversational interface — without needing
DevOps knowledge.

This is the dummy/stub version: no real cloud calls are made. It captures the
form fields, validates that required ones are present, fills defaults for
omitted optional fields, and returns an acknowledgment as if the resource had
been created.

Field naming follows the chat-bot-POC vance form metadata so the schemas line
up with what the broader DevLift platform already uses.
"""

from datetime import datetime, timezone
from typing import Any
import uuid

from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Resource catalog
# ---------------------------------------------------------------------------
# Each entry describes the form fields the LLM should collect from the user
# before calling provision_resource().
#   - "required" fields MUST be present in the attributes dict.
#   - "optional" fields are filled with defaults if the caller omits them.
#
# Adding a new resource type = adding one entry here. No tool changes needed.

RESOURCE_CATALOG: dict[str, dict[str, Any]] = {
    "s3_bucket": {
        "title": "S3 Bucket",
        "description": "Object storage bucket for files, backups, and static assets.",
        "required": {
            "bucket_name": "Globally-unique bucket name. 3-63 chars, lowercase, numbers, dots, hyphens.",
        },
        "optional": {
            "versioning": ("bool", False),
            "replication": ("bool", False),
            # Conditional: required only when replication=True (12-digit AWS account id).
            "cross_account_id": ("str", None),
        },
    },
    "sqs_queue": {
        "title": "SQS Queue",
        "description": "Managed message queue for asynchronous processing.",
        "required": {
            "queue_name": "Queue name. 1-80 chars; FIFO queues must end with .fifo.",
            "fifo": "Whether this is a FIFO queue (true | false).",
            "dlq": "Whether to enable a Dead Letter Queue (true | false).",
            "cross_account_ids": "List of 12-digit AWS account IDs allowed cross-account access.",
        },
        "optional": {
            "max_receive_count": ("int", 10),
            "visibility_timeout": ("int", 30),       # seconds, 0–43200
            "main_retention": ("int", 345600),       # seconds, 60–1209600
            "dlq_retention": ("int", 1209600),       # seconds, 60–1209600
        },
    },
    "dynamodb_table": {
        "title": "DynamoDB Table",
        "description": "Serverless NoSQL key/value store.",
        "required": {
            "table_name": "Table name. 3-255 chars, alphanumeric, dot, hyphen.",
            "partition_key": "Partition key attribute name.",
            "partition_key_type": "Partition key type (String | Number | Binary).",
        },
        "optional": {},
    },
    "database": {
        "title": "Database",
        "description": "Relational database created on a managed DB server (RDS / Aurora / in-cluster).",
        "required": {
            "database_name": "Database name. 1-63 chars, alphanumeric and underscores only.",
            "server_name": "Target DB server identifier (chosen for the env/geo).",
        },
        "optional": {},
    },
    "eks_service": {
        "title": "EKS Service",
        "description": "Containerized application hosted on EKS (Kubernetes).",
        "required": {
            "service_name": "Name of the service.",
            "repository": "Git repository URL.",
            "branch": "Git branch to deploy from.",
            "language": "Application language (java | go | python | node).",
        },
        "optional": {
            "port": ("int", None),  # filled from language defaults below
            "cpu_request": ("str", "500m"),
            "cpu_limit": ("str", "1000m"),
            "memory_request": ("str", "512Mi"),
            "memory_limit": ("str", "1024Mi"),
            "replicas": ("int", 1),
            "namespace": ("str", None),
        },
    },
    "ecs_service": {
        "title": "ECS Service",
        "description": "Containerized application hosted on ECS Fargate.",
        "required": {
            "service_name": "Name of the service.",
            "repository": "Git repository URL.",
            "branch": "Git branch to deploy from.",
        },
        "optional": {
            "cpu_vcpu": ("float", 0.5),
            "ram_gb": ("float", 1.0),
            "desired_count": ("int", 1),
            "alb_selection": ("str", "no_alb"),  # no_alb | existing_alb | create_new_alb
        },
    },
}

# --- TEMPORARILY SCOPED TO SERVICE DEPLOYMENT ONLY ---
# We're rolling out resource types one at a time. The full catalog above
# defines every resource we'll eventually support, but only the service-
# deployment entries (eks_service, ecs_service) are currently exposed
# through the MCP tools. Add a type back to ENABLED_TYPES to re-enable it.
ENABLED_TYPES = {"eks_service", "ecs_service"}
RESOURCE_CATALOG = {k: v for k, v in RESOURCE_CATALOG.items() if k in ENABLED_TYPES}


LANGUAGE_DEFAULT_PORTS = {
    "java": 8080,
    "go": 8080,
    "python": 8000,
    "node": 3000,
}

# Common placement parameters that every resource needs, mirroring the
# placement_parameters block in the chat-bot-POC form metadata.
SUPPORTED_PRODUCTS = ["core", "falcon"]
SUPPORTED_ENVIRONMENTS = ["stage", "prod"]
SUPPORTED_GEO_LOCATIONS = ["Mumbai", "London", "Canada"]


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------

DEVLIFT_INSTRUCTIONS = """\
DevLift MCP Server — instructions for the assistant.

This server lets app developers deploy their service without needing to know DevOps. When the user asks you to deploy ("deploy this", "deploy my app", "ship this service", etc.), follow this workflow:

1. SCAN THE CODEBASE FIRST. Before calling any deployment tool, infer as many form fields as you can from the user's project so they don't have to type them. Look for:
   - language: from file extensions and package manifests (pyproject.toml / requirements.txt → python; go.mod → go; pom.xml / build.gradle → java; package.json → node).
   - language_version: from the same manifests (pyproject.toml requires-python, go.mod `go` directive, package.json engines.node, pom.xml maven.compiler.source).
   - repository: run `git remote get-url origin`.
   - branch: run `git branch --show-current`.
   - service_name: from package.json `name`, pyproject.toml `[project].name`, or the project directory name as a fallback.
   - port: scan entry files for explicit port literals (FastAPI/Flask `port=`, Express `app.listen(...)`, Spring `server.port=`, Go `http.ListenAndServe(":...")`). If unsure, leave it out — the server applies a sensible language default.
   - Dockerfile: check whether a `Dockerfile` exists at the project root.

2. CALL describe_resource("eks_service") (or "ecs_service") to learn the exact required fields. Default to eks_service unless the user explicitly says ECS.

3. ASK THE USER for ONLY the fields you couldn't infer, plus the placement fields:
   - product: core | falcon
   - environment: stage | prod
   - geo_location: Mumbai | London | Canada
   Show the auto-filled values briefly so the user can confirm or correct them. Don't enumerate every field — only what you inferred from the codebase scan.

4. CALL provision_resource(resource_type, attributes, product, environment, geo_location) with the complete attributes dict.

5. CONFIRM to the user: "Your service has been deployed successfully" with the resource_id and placement (geo + environment).

STYLE:
- Plain language. The user is an app developer, NOT a DevOps engineer.
- Don't re-ask for things you already inferred from the code — show them what you found instead and ask for confirmation.
- Don't lecture about Kubernetes / Fargate / IAM unless the user asks.
- This is currently a DUMMY environment — provision_resource returns acknowledgments but doesn't actually create cloud resources. Only mention this if the user explicitly asks.
"""


mcp = FastMCP("devlift-mcp-server", instructions=DEVLIFT_INSTRUCTIONS)


@mcp.tool()
def list_supported_resources() -> dict[str, Any]:
    """
    List every resource type the developer can provision through this server.
    Call this first if you don't already know what's available.
    """
    return {
        "resources": [
            {
                "type": rtype,
                "title": rdef["title"],
                "description": rdef["description"],
            }
            for rtype, rdef in RESOURCE_CATALOG.items()
        ],
        "common_placement_fields": {
            "product": SUPPORTED_PRODUCTS,
            "environment": SUPPORTED_ENVIRONMENTS,
            "geo_location": SUPPORTED_GEO_LOCATIONS,
        },
    }


@mcp.tool()
def describe_resource(resource_type: str) -> dict[str, Any]:
    """
    Return the form fields the developer must (and may optionally) provide in
    order to provision the given resource type. Use this to know what to ask
    the user before calling provision_resource().

    Args:
        resource_type: One of the types from list_supported_resources().
    """
    rdef = RESOURCE_CATALOG.get(resource_type)
    if not rdef:
        return {
            "error": f"Unknown resource_type '{resource_type}'.",
            "supported": list(RESOURCE_CATALOG.keys()),
        }

    return {
        "resource_type": resource_type,
        "title": rdef["title"],
        "description": rdef["description"],
        "required_fields": rdef["required"],
        "optional_fields": {
            name: {"type": typ, "default": default}
            for name, (typ, default) in rdef["optional"].items()
        },
        "placement_fields": {
            "product": f"One of {SUPPORTED_PRODUCTS}",
            "environment": f"One of {SUPPORTED_ENVIRONMENTS}",
            "geo_location": f"One of {SUPPORTED_GEO_LOCATIONS}",
        },
        "next_step": (
            "Collect every required field from the user, plus product, "
            "environment and geo_location, then call provision_resource()."
        ),
    }


@mcp.tool()
def provision_resource(
    resource_type: str,
    attributes: dict[str, Any],
    product: str,
    environment: str,
    geo_location: str,
) -> dict[str, Any]:
    """
    DUMMY provisioning. Validates that the form is complete, fills defaults
    for omitted optional fields, and returns an acknowledgment as if the
    resource had been created. No real cloud calls are made.

    Args:
        resource_type: Resource type from list_supported_resources().
        attributes: Dict of field name -> value, covering at minimum every
            required field returned by describe_resource(resource_type).
        product: Target product (core | falcon).
        environment: Target environment (stage | prod).
        geo_location: Target geo / region (Mumbai | London | Canada).
    """
    rdef = RESOURCE_CATALOG.get(resource_type)
    if not rdef:
        return {
            "status": "error",
            "error": f"Unknown resource_type '{resource_type}'.",
            "supported": list(RESOURCE_CATALOG.keys()),
        }

    if product not in SUPPORTED_PRODUCTS:
        return {
            "status": "error",
            "error": f"Unsupported product '{product}'.",
            "supported": SUPPORTED_PRODUCTS,
        }

    if environment not in SUPPORTED_ENVIRONMENTS:
        return {
            "status": "error",
            "error": f"Unsupported environment '{environment}'.",
            "supported": SUPPORTED_ENVIRONMENTS,
        }

    if geo_location not in SUPPORTED_GEO_LOCATIONS:
        return {
            "status": "error",
            "error": f"Unsupported geo_location '{geo_location}'.",
            "supported": SUPPORTED_GEO_LOCATIONS,
        }

    missing = [
        f for f in rdef["required"]
        if f not in attributes or attributes[f] in (None, "", [])
    ]

    # Conditional rule: S3 cross_account_id is required when replication=True.
    if resource_type == "s3_bucket" and attributes.get("replication") in (True, "true"):
        if not attributes.get("cross_account_id"):
            missing.append("cross_account_id")

    if missing:
        return {
            "status": "incomplete",
            "missing_required_fields": missing,
            "hint": (
                "Ask the developer for these values, then call "
                "provision_resource() again with the complete attributes dict."
            ),
        }

    # Fill defaults for any optional field the caller didn't supply.
    resolved = dict(attributes)
    for name, (_typ, default) in rdef["optional"].items():
        if name not in resolved:
            resolved[name] = default

    # Language-default port for eks_service when caller didn't specify one.
    if resource_type == "eks_service" and resolved.get("port") is None:
        lang = str(resolved.get("language", "")).lower()
        resolved["port"] = LANGUAGE_DEFAULT_PORTS.get(lang)

    resource_id = f"{resource_type}-{uuid.uuid4().hex[:8]}"
    return {
        "status": "acknowledged",
        "resource_id": resource_id,
        "resource_type": resource_type,
        "placement": {
            "product": product,
            "environment": environment,
            "geo_location": geo_location,
        },
        "attributes": resolved,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "message": (
            f"[DUMMY] {rdef['title']} '{resource_id}' has been created in "
            f"{geo_location} ({product}/{environment}). No real cloud "
            f"resources were provisioned — this is a stub for development "
            f"purposes."
        ),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
