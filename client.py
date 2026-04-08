"""
client.py — DevLift MCP Client.

Connects to server.py over stdio and bridges tool calls between an OpenAI LLM
and the DevLift MCP server. Lets app developers describe what infrastructure
they need in plain language; the LLM walks them through the form fields and
calls provision_resource() on their behalf.

Flow (mirrors ai-workshop/mcp_client.py):
  1. Launch server.py as a subprocess (stdio transport)
  2. Call list_tools() to discover the server's tools
  3. Convert MCP tool schemas → OpenAI function-calling format
  4. Pre-fetch the resource catalog once via list_supported_resources so the
     LLM knows what's available without an extra round-trip
  5. Chat loop: user → LLM → (tool calls?) → MCP server → LLM → user

Run:
    python client.py
"""

import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

load_dotenv()


# ---------------------------------------------------------------------------
# Convert MCP tool schema → OpenAI function-calling format
# ---------------------------------------------------------------------------

def mcp_tools_to_openai_format(mcp_tools) -> list[dict]:
    """
    The MCP server returns tools in MCP schema format. OpenAI expects a
    different shape. This function bridges the two.

    MCP tool shape:
        { name, description, inputSchema: { type, properties, required } }

    OpenAI tool shape:
        { type: "function", function: { name, description, parameters: { ... } } }
    """
    openai_tools = []
    for tool in mcp_tools:
        openai_tools.append({
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            },
        })
    return openai_tools


# ---------------------------------------------------------------------------
# Main async logic
# ---------------------------------------------------------------------------

async def main():
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    model = os.getenv("OPENAI_MODEL", "gpt-4o")

    # ------------------------------------------------------------------
    # Step 1: Launch the MCP server as a subprocess over stdio
    # ------------------------------------------------------------------
    server_params = StdioServerParameters(
        command=sys.executable,          # same Python interpreter
        args=["server.py"],              # the server script
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Handshake — required by the MCP protocol
            await session.initialize()

            # ----------------------------------------------------------
            # Step 2: Discover tools from the server
            # ----------------------------------------------------------
            tools_result = await session.list_tools()
            mcp_tools = tools_result.tools

            print(f"Connected to DevLift MCP server. Discovered {len(mcp_tools)} tools:")
            for t in mcp_tools:
                desc = (t.description or "").strip().split("\n")[0]
                print(f"  - {t.name}: {desc[:70]}")

            # Convert to OpenAI format so we can pass them to the LLM
            openai_tools = mcp_tools_to_openai_format(mcp_tools)

            # ----------------------------------------------------------
            # Step 3: Pre-fetch the resource catalog so the LLM knows
            # what's available without spending a turn on discovery.
            # ----------------------------------------------------------
            print("\nLoading resource catalog from MCP server...")
            catalog_result = await session.call_tool("list_supported_resources", {})
            catalog_text = catalog_result.content[0].text
            print("Catalog loaded.\n")

            # Build tool descriptions dynamically from discovered tools
            tool_descriptions = "\n".join(
                f"   - {t.name}: {(t.description or '').strip().splitlines()[0]}"
                for t in mcp_tools
            )

            # ----------------------------------------------------------
            # Step 4: System prompt
            # ----------------------------------------------------------
            system_prompt = f"""You are DevLift, an infrastructure provisioning assistant for application developers.

Your job is to help developers provision cloud infrastructure (S3 buckets, SQS queues, DynamoDB tables, databases, EKS services, ECS services) WITHOUT requiring them to know DevOps. They describe what they need in natural language; you walk them through the required configuration and submit it on their behalf.

WORKFLOW:
1. When the user describes a need (e.g. "I need an S3 bucket for service files"), figure out which resource type from the catalog matches.
2. Call describe_resource(resource_type) to learn exactly what fields you need to collect.
3. Ask the user for the required fields ONE OR TWO AT A TIME — never dump the whole form on them. Use plain language, not raw field IDs.
4. Also collect the common placement fields: product (core | falcon), environment (stage | prod), and geo_location (Mumbai | London | Canada).
5. The user may give multiple values in one message ("core prod mumbai my-bucket"). Extract everything you can recognize. Don't reject "out of order" answers.
6. Apply sensible defaults for optional fields silently — only ask about them if the user wants to customize.
7. Once you have everything, call provision_resource with the complete attributes dict plus product, environment, and geo_location.
8. If the response is status:"incomplete", ask the user for the missing fields and call provision_resource again.
9. On success, confirm to the user with the resource_id and where it was created.

STYLE:
- Friendly, plain-language. The user is an app developer, NOT a DevOps engineer.
- Explain fields the way a developer would understand them. E.g. "FIFO means messages are processed in the exact order they're sent — say yes if ordering matters."
- Don't lecture. Don't overwhelm with optional fields.

NOTE: This is currently a DUMMY environment — provision_resource returns acknowledgments but doesn't actually create cloud resources. Mention this only if the user asks.

AVAILABLE TOOLS:
{tool_descriptions}

RESOURCE CATALOG (loaded at startup):
{catalog_text}
"""

            messages: list = [{"role": "system", "content": system_prompt}]

            # ----------------------------------------------------------
            # Step 5: Chat loop
            # ----------------------------------------------------------
            print("DevLift Infra Assistant ready. Tell me what you need, or type 'exit' to quit.\n")

            while True:
                try:
                    user_input = input("You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye!")
                    break

                if not user_input:
                    continue
                if user_input.lower() in ("exit", "quit"):
                    print("Goodbye!")
                    break

                messages.append({"role": "user", "content": user_input})

                # Agentic loop — keep going until the LLM stops calling tools
                while True:
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=openai_tools,
                        tool_choice="auto",
                    )

                    reply = response.choices[0].message
                    messages.append(reply)

                    if reply.tool_calls:
                        # Forward each tool call to the MCP server
                        for tool_call in reply.tool_calls:
                            name = tool_call.function.name
                            args = json.loads(tool_call.function.arguments)

                            print(f"\n  [tool]   {name}")
                            print(f"  [args]   {json.dumps(args, indent=2)}")

                            # --- MCP call_tool instead of manual dispatch ---
                            result = await session.call_tool(name, args)
                            tool_output = result.content[0].text

                            print(f"  [result] {tool_output[:200]}")

                            messages.append({
                                "role": "tool",
                                "tool_call_id": tool_call.id,
                                "content": tool_output,
                            })
                    else:
                        # No more tool calls — print final answer
                        print(f"\nAssistant: {reply.content}\n")
                        break


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
