# Verum Signal MCP Server

Exposes Verum Signal as a Model Context Protocol (MCP) tool server so Claude and other agents can call it natively.

## Tools

| Tool | Description |
|------|-------------|
| `get_outlet_score` | Credibility score + verdict breakdown for any news outlet |
| `search_claims` | Search verified claims by topic or keyword |
| `get_debate_verdicts` | All verified claims from a specific political debate |
| `list_debates` | List available debates |
| `get_api_status` | Corpus statistics |

## Setup

1. Get an API key at https://verumsignal.com/developers
2. Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "verum-signal": {
      "command": "python3",
      "args": ["/absolute/path/to/mcp_server.py"],
      "env": {
        "VS_API_KEY": "vs_live_your_key_here"
      }
    }
  }
}
```

3. Restart Claude Desktop — Verum Signal tools will appear automatically.

## Example prompts

- "What's the credibility score for foxnews.com?"
- "Find verified claims about immigration policy"
- "What claims were disputed in the Colorado governor debate?"
