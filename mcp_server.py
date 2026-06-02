#!/usr/bin/env python3
"""
Verum Signal MCP Server
Exposes Verum Signal as a Model Context Protocol tool server.
Agents (Claude, GPT, etc.) can call these tools natively.

Usage:
  python3 mcp_server.py

Environment:
  VS_API_KEY  — Verum Signal API key (vs_live_...)
  VS_API_BASE — API base URL (default: https://api.verumsignal.com)
"""
import json, os, sys
import urllib.request, urllib.parse

VS_API_BASE = os.environ.get("VS_API_BASE", "https://api.verumsignal.com")
VS_API_KEY  = os.environ.get("VS_API_KEY", "")

def _api(path, params=None):
    url = VS_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {VS_API_KEY}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"error": e.reason, "status": e.code}
    except Exception as e:
        return {"error": str(e)}

# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_outlet_score",
        "description": (
            "Get the credibility score and verdict breakdown for a news outlet. "
            "Returns a score from 0-100, tier (published/stabilizing/limited_data/tracked), "
            "and counts of each verdict type. Use this to assess source reliability."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "domain": {
                    "type": "string",
                    "description": "The outlet domain, e.g. 'nytimes.com', 'foxnews.com', 'bbc.com'"
                }
            },
            "required": ["domain"]
        }
    },
    {
        "name": "search_claims",
        "description": (
            "Search Verum Signal's database of independently verified factual claims. "
            "Returns claims with verdicts (supported/plausible/corroborated/overstated/disputed/"
            "not_supported/not_verifiable/opinion), confidence scores, and sources. "
            "Use this to fact-check specific claims or find evidence on a topic."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query — topic, keyword, or specific claim text"
                },
                "verdict": {
                    "type": "string",
                    "description": "Filter by verdict: supported, disputed, overstated, not_supported, etc.",
                    "enum": ["supported","plausible","corroborated","overstated","disputed","not_supported","not_verifiable","opinion"]
                },
                "outlet": {
                    "type": "string",
                    "description": "Filter by outlet domain, e.g. foxnews.com"
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results (1-50, default 10)",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_debate_verdicts",
        "description": (
            "Get verified claims from a political debate. Returns claims with speaker attribution, "
            "verdicts, and evidence. Use this to fact-check debate statements."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "slug": {
                    "type": "string",
                    "description": "Debate slug, e.g. 'colorado-gov-rep-2026-r3'. Use list_debates to find slugs."
                }
            },
            "required": ["slug"]
        }
    },
    {
        "name": "list_debates",
        "description": "List available political debates with claim counts and status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "get_api_status",
        "description": "Get Verum Signal corpus statistics: total articles, claims, and verified claim counts.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
]

# ── Tool handlers ─────────────────────────────────────────────────────────────

def handle_get_outlet_score(args):
    domain = args.get("domain", "").lower().strip()
    if not domain:
        return {"error": "domain required"}
    result = _api(f"/v1/outlets/{domain}")
    if "error" in result:
        return result
    return {
        "domain": domain,
        "score": result.get("score"),
        "tier": result.get("tier"),
        "scoreable_claims": result.get("total_scoreable_claims"),
        "verdict_counts": result.get("verdict_counts", {}),
        "methodology_version": result.get("methodology_version"),
        "leaderboard_url": f"https://verumsignal.com/outlet/{domain}",
    }

def handle_search_claims(args):
    params = {"limit": min(args.get("limit", 10), 50)}
    if args.get("verdict"):
        params["verdict"] = args["verdict"]
    if args.get("outlet"):
        params["outlet"] = args["outlet"]
    # Claims endpoint doesn't have text search yet — return recent claims filtered
    result = _api("/v1/claims", params)
    if "error" in result:
        return result
    query = args.get("query", "").lower()
    claims = result.get("data", [])
    # Client-side filter by query text
    if query:
        claims = [c for c in claims if query in (c.get("claim_text") or "").lower()]
    return {
        "query": args.get("query"),
        "count": len(claims),
        "claims": [{
            "id": c.get("id"),
            "claim_text": c.get("claim_text"),
            "verdict": c.get("verdict"),
            "confidence_score": c.get("confidence_score"),
            "verdict_summary": c.get("verdict_summary"),
            "outlet": c.get("outlet"),
            "methodology_version": c.get("methodology_version"),
        } for c in claims[:20]]
    }

def handle_get_debate_verdicts(args):
    slug = args.get("slug", "").lower().strip()
    if not slug:
        return {"error": "slug required"}
    result = _api(f"/v1/debates/{slug}/claims")
    if "error" in result:
        return result
    claims = result.get("data", [])
    return {
        "slug": slug,
        "count": len(claims),
        "claims": [{
            "claim_text": c.get("claim_text"),
            "speaker": c.get("speaker"),
            "verdict": c.get("verdict"),
            "confidence_score": c.get("confidence_score"),
            "verdict_summary": c.get("verdict_summary"),
            "is_provisional": c.get("is_provisional"),
        } for c in claims]
    }

def handle_list_debates(args):
    result = _api("/v1/debates")
    if "error" in result:
        return result
    return {"debates": result.get("data", [])}

def handle_get_api_status(args):
    return _api("/v1/meta")

HANDLERS = {
    "get_outlet_score":    handle_get_outlet_score,
    "search_claims":       handle_search_claims,
    "get_debate_verdicts": handle_get_debate_verdicts,
    "list_debates":        handle_list_debates,
    "get_api_status":      handle_get_api_status,
}

# ── MCP protocol (stdio) ──────────────────────────────────────────────────────

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

def handle_message(msg):
    method = msg.get("method")
    msg_id = msg.get("id")

    if method == "initialize":
        send({"jsonrpc":"2.0","id":msg_id,"result":{
            "protocolVersion":"2024-11-05",
            "capabilities":{"tools":{}},
            "serverInfo":{"name":"verum-signal","version":"1.0.0"}
        }})

    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":msg_id,"result":{"tools":TOOLS}})

    elif method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        if tool_name not in HANDLERS:
            send({"jsonrpc":"2.0","id":msg_id,"error":{"code":-32601,"message":f"Unknown tool: {tool_name}"}})
            return
        if not VS_API_KEY:
            send({"jsonrpc":"2.0","id":msg_id,"error":{"code":-32000,"message":"VS_API_KEY not set"}})
            return
        result = HANDLERS[tool_name](tool_args)
        send({"jsonrpc":"2.0","id":msg_id,"result":{
            "content":[{"type":"text","text":json.dumps(result,indent=2)}]
        }})

    elif method == "notifications/initialized":
        pass  # no response needed

    else:
        if msg_id is not None:
            send({"jsonrpc":"2.0","id":msg_id,"error":{"code":-32601,"message":f"Method not found: {method}"}})

def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            handle_message(msg)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            sys.stderr.write(f"Error: {e}\n")

if __name__ == "__main__":
    main()
