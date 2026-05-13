"""
LangChain agent that uses the Calendar MCP server to schedule meetings.

The agent supports two connection modes controlled by env var MCP_SERVER_URL:

  Mode A — stdio (local dev, default when MCP_SERVER_URL is not set):
    The agent spawns the MCP server as a local subprocess.
    No authentication required. Used for local testing.

  Mode B — HTTP (production, when MCP_SERVER_URL is set):
    The agent connects to the deployed Azure Container Apps MCP server over
    HTTPS. It first obtains an Azure AD token for the MCP server's App
    Registration (MCP_SERVER_CLIENT_ID), then passes it as a Bearer header
    on every MCP tool call. Only callers with a valid token from YOUR tenant
    can reach the server.

    Required env vars for HTTP mode:
      MCP_SERVER_URL        = https://<internal-fqdn>/mcp/
      MCP_SERVER_CLIENT_ID  = <App Registration client-id of the MCP server>
      AZURE_TENANT_ID       = <your tenant id>
      AZURE_CLIENT_ID       = <this agent's app client-id>
      AZURE_CLIENT_SECRET   = <this agent's app client-secret>
"""

from __future__ import annotations

import asyncio
import os
import sys

import msal
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import AzureChatOpenAI, ChatOpenAI
from langgraph.prebuilt import create_react_agent

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are an intelligent meeting scheduler.

You have access to three calendar tools:
- check_availability   – raw free/busy for a list of users and a time range
- find_meeting_slots   – ranked list of candidate slots (best first)
- get_attendee_summary – plain-English overview of who is free/busy

Your workflow:
1. Call get_attendee_summary to get a quick overview of the group.
2. Call find_meeting_slots with the desired duration and date range.
3. Pick the BEST slot — maximise free attendees, prefer mornings, prefer
   earlier in the week.
4. Explain your recommendation clearly:
   - State the proposed time (include timezone label "UTC").
   - List who will be present and who has a conflict.
   - Suggest an alternative if the best slot still has conflicts.

Always be concise and actionable.
""".strip()


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def _build_llm():
    """Return a chat model based on env vars.

    Set OPENAI_API_KEY for OpenAI, or set AZURE_OPENAI_* vars for Azure.
    """
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
    if azure_endpoint:
        return AzureChatOpenAI(
            azure_endpoint=azure_endpoint,
            azure_deployment=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
            openai_api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            temperature=0,
        )
    return ChatOpenAI(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o"),
        temperature=0,
    )


# ---------------------------------------------------------------------------
# Azure AD token helper (for HTTP / production mode)
# ---------------------------------------------------------------------------

def _get_agent_token_for_mcp() -> str:
    """
    Obtain a token that allows THIS agent to call the MCP server.

    The agent authenticates as its own Azure AD App Registration
    (AZURE_CLIENT_ID / AZURE_CLIENT_SECRET) and requests a scope
    scoped to the MCP server's App Registration (MCP_SERVER_CLIENT_ID).

    This token is verified by AzureADAuthMiddleware in the MCP server —
    only tokens from your tenant are accepted.
    """
    tenant_id = os.environ["AZURE_TENANT_ID"]
    client_id = os.environ["AZURE_CLIENT_ID"]
    client_secret = os.environ["AZURE_CLIENT_SECRET"]
    mcp_client_id = os.environ["MCP_SERVER_CLIENT_ID"]

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )

    # Scope format: api://<mcp-server-client-id>/.default
    result = app.acquire_token_for_client(
        scopes=[f"api://{mcp_client_id}/.default"]
    )

    if "access_token" not in result:
        error = result.get("error_description", result.get("error", "unknown"))
        raise RuntimeError(f"Failed to get MCP server token: {error}")

    return result["access_token"]


# ---------------------------------------------------------------------------
# MCP config builder
# ---------------------------------------------------------------------------

def _build_mcp_config() -> dict:
    """
    Return the MultiServerMCPClient config dict.

    Priority:
      1. MCP_SERVER_URL set + SKIP_AUTH=true  → HTTP, no token (trial/personal account mode)
      2. MCP_SERVER_URL set                   → HTTP with Azure AD Bearer token (production)
      3. Neither                              → stdio subprocess (local dev)
    """
    server_url = os.environ.get("MCP_SERVER_URL", "")

    if server_url:
        headers = {}
        skip_auth = os.environ.get("SKIP_AUTH", "").lower() in ("1", "true", "yes")
        if not skip_auth:
            token = _get_agent_token_for_mcp()
            headers = {"Authorization": f"Bearer {token}"}

        return {
            "calendar": {
                "url": server_url.rstrip("/") + "/",
                "transport": "streamable_http",
                "headers": headers,
            }
        }
    else:
        server_script = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "mcp_server", "server.py")
        )
        return {
            "calendar": {
                "command": sys.executable,
                "args": [server_script],
                "transport": "stdio",
            }
        }


# ---------------------------------------------------------------------------
# Agent runner
# ---------------------------------------------------------------------------

async def run_scheduling_agent(user_request: str) -> str:
    """
    Connect to the MCP server (stdio or HTTP), wire into LangChain, and run
    the agent with the given user request.

    Args:
        user_request: Natural-language scheduling request.

    Returns:
        The agent's final answer as a string.
    """
    mcp_config = _build_mcp_config()

    async with MultiServerMCPClient(mcp_config) as mcp_client:
        tools = mcp_client.get_tools()

        llm = _build_llm()
        agent = create_react_agent(llm, tools)

        messages = [
            SystemMessage(content=SYSTEM_PROMPT),
            HumanMessage(content=user_request),
        ]

        result = await agent.ainvoke({"messages": messages})

        # The last message in the chain is the final answer
        final_message = result["messages"][-1]
        return final_message.content


# ---------------------------------------------------------------------------
# Convenience sync wrapper (for scripts / REPL)
# ---------------------------------------------------------------------------

def schedule(user_request: str) -> str:
    """Synchronous wrapper around run_scheduling_agent."""
    return asyncio.run(run_scheduling_agent(user_request))
