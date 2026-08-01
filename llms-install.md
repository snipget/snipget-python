# llms-install.md — Snipget setup guide for AI agents

This guide is for AI agents (Cline, Claude Code, Cursor, or any MCP client) setting up **Snipget**: 300+ deterministic data utilities — validation, normalization, parsing, matching, and PII redaction for names, addresses, phones, emails, dates, and identifiers, plus healthcare, chemistry, and biotech packs. No LLM inside; every tool is a pure function with a consistent response envelope.

## Primary path: connect the remote MCP server (no local install)

There is nothing to download, build, or run locally. Snipget is a hosted remote MCP server:

- **Endpoint:** `https://mcp.snipget.ai/mcp`
- **Transport:** streamable-http
- **Auth:** OAuth 2.0 with Dynamic Client Registration (DCR). The MCP client registers itself and opens a browser login automatically — no pre-provisioned client ID, no API key, no manual token. The user signs in or creates a free account at [snipget.ai](https://snipget.ai) in the browser window; the free tier activates immediately.

### Cline

Add this entry to the `mcpServers` object in `cline_mcp_settings.json` — open it via the MCP Servers icon → Configure MCP Servers (the button opens the file at its platform-specific location). Merge with any existing servers; don't replace the file:

```json
{
  "mcpServers": {
    "snipget": {
      "type": "streamableHttp",
      "url": "https://mcp.snipget.ai/mcp"
    }
  }
}
```

Cline handles the OAuth flow on first use — approve the browser prompt and you are connected. (`streamableHttp` is Cline's spelling of the streamable-http transport; Claude Code's CLI calls the same thing `--transport http`.)

### Claude Code

```bash
claude mcp add --transport http snipget https://mcp.snipget.ai/mcp
```

Then run `/mcp` inside a session to complete the OAuth login once.

### Cursor

One-click: [install snipget in Cursor](https://cursor.com/install-mcp?name=snipget&config=eyJ1cmwiOiJodHRwczovL21jcC5zbmlwZ2V0LmFpL21jcCJ9)

Or add manually to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "snipget": {
      "url": "https://mcp.snipget.ai/mcp"
    }
  }
}
```

Cursor prompts for OAuth on first use.

### Claude Desktop / claude.ai

Settings → Connectors → Add custom connector → URL `https://mcp.snipget.ai/mcp`.

### Any other MCP client

Use the generic remote form — URL `https://mcp.snipget.ai/mcp`, transport streamable-http, OAuth discovery per the MCP spec. Unauthenticated requests return 401 with the standard OAuth metadata pointers; clients that implement MCP authorization negotiate the rest.

## Verify the connection

1. The client shows `snipget` as connected after the OAuth browser step.
2. Call the `catalog_search_semantic` tool with a task description (e.g. `"validate a phone number"`) — it returns matching tools from the catalog.
3. Call a returned tool. Every response has the shape `{status, confidence, result, meta}` with `status: "ok"` on success.

Semantic catalog discovery is the intended workflow: search for what you need instead of loading all 300+ tools into context.

## Secondary path: REST API with an API key

For plain HTTPS usage without MCP:

```bash
pip install snipget-client
```

```python
from snipget import Client

client = Client(api_key="YOUR_API_KEY")  # or set SNIPGET_API_KEY
resp = client.call("/healthcare/npi/validate", {"npi": "1234567893"})
print(resp.result)
```

Get an API key at [snipget.ai](https://snipget.ai) (free tier). The [OpenAPI spec](https://api.snipget.ai/openapi.json) and [interactive docs](https://api.snipget.ai/docs) are the per-endpoint contract.

## Troubleshooting

- **401 on connect:** OAuth not completed yet — trigger the client's login/reauthorize action and finish the browser step.
- **Client has no streamable-http support, or no built-in OAuth handling** (older Cline/client builds): use a stdio→HTTP MCP proxy that handles both (e.g. `mcp-remote`): command `npx`, args `["-y", "mcp-remote", "https://mcp.snipget.ai/mcp"]` — or fall back to the REST path above.
- **No API key is needed for the MCP path.** The API-key instructions in the README apply only to the REST/SDK path.
- **429 responses:** per-second throttle (retry after `Retry-After`) or monthly free-tier quota — upgrade at snipget.ai.

## Links

- Website: https://snipget.ai · Docs: https://snipget.ai/docs · Support: support@snipget.ai
- OpenAPI: https://api.snipget.ai/openapi.json · API reference: https://api.snipget.ai/redoc
