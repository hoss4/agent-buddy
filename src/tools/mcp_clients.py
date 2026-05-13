import asyncio
import os
from dotenv import load_dotenv
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.session import ClientSession


load_dotenv(override=True)

# api_key = os.getenv('API_KEY1')
# base_url = os.getenv('BASE_URL')

async def verify_server(server_name: str, command: str, args: list[str], env: dict):
    """Starts an MCP server process and asks it to list its tools."""
    print(f"\n--- Testing Connection to {server_name} ---")
    
    server_params = StdioServerParameters(
        command=command,
        args=args,
        env=env
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                
                tools_response = await session.list_tools()
                
                print("connected to calendar mcp succesfully")
                print("Available Tools:")
                for tool in tools_response.tools:
                    # Safely handle missing descriptions
                    description = tool.description[:60] if tool.description else "No description"
                    print(f"  - {tool.name}: {description}...")
                    
    except Exception as e:
        print(f" Failed to connect to {server_name}.")
        print(f"Error: {e}")

async def main():
    # 1. Test Jira MCP Server (Using the stable community package)
    email     = os.getenv("JIRA_EMAIL", "")
    api_token = os.getenv("JIRA_API_TOKEN", "")
    base_url  = os.getenv("JIRA_BASE_URL", "").rstrip("/")


    jira_env = os.environ.copy()
    jira_env["JIRA_URL"]       = base_url
    jira_env["JIRA_USERNAME"]  = email
    jira_env["JIRA_API_TOKEN"] = api_token
    
    await verify_server(
        server_name="Jira MCP",
        command="uvx",
        args=["mcp-atlassian"], 
        env=jira_env
    )

    # 2. Test Google Workspace MCP Server (Using the stable calendar package)
    google_env = os.environ.copy()
    google_env["GOOGLE_CLIENT_ID"] = os.getenv("GOOGLE_CLIENT_ID", "")
    google_env["GOOGLE_CLIENT_SECRET"] = os.getenv("GOOGLE_CLIENT_SECRET", "")
    google_env["GOOGLE_REFRESH_TOKEN"] = os.getenv("GOOGLE_REFRESH_TOKEN", "")
    print("here")
    print(os.getenv("GOOGLE_CLIENT_ID", ""))
    await verify_server(
        server_name="Google Calendar MCP",
        command="npx", 
        args=["-y", "@gongrzhe/server-calendar-mcp"], 
        env=google_env
    )

if __name__ == "__main__":
    asyncio.run(main())