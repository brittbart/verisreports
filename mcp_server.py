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
import urllib.request, urllib.parse, urllib.error

VS_API_BASE = os.environ.get("VS_API_BASE", "https://api.verumsignal.com")
VS_API_KEY  = os.environ.get("VS_API_KEY", "")

# Quota/rate-limit headers the REST API returns on every authenticated call.
# Passed through into every tool's response so an agent running this
# self-hosted server (and therefore owning its own key) can actually see
# what's left, instead of finding out by hitting a 429 with no warning.
_QUOTA_HEADERS = {
    "X-RateLimit-Limit":      "rate_limit_per_minute",
    "X-RateLimit-Remaining":  "rate_limit_remaining",
    "X-Quota-Limit":          "monthly_quota_limit",
    "X-Quota-Remaining":      "monthly_quota_remaining",
}


def _quota_from_headers(headers):
    quota = {}
    for header_name, key in _QUOTA_HEADERS.items():
        v = headers.get(header_name)
        if v is not None:
            quota[key] = v
    return quota


def _api(path, params=None):
    """Call the REST API. Returns the parsed JSON body with a "_quota" key
    attached whenever rate-limit/quota headers were present. On error,
    passes through the REAL error body from the API (not just the generic
    HTTP reason phrase) alongside the same quota info."""
    url = VS_API_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {VS_API_KEY}",
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
            quota = _quota_from_headers(r.headers)
            if quota:
                data["_quota"] = quota
            return data
    except urllib.error.HTTPError as e:
        body_bytes = e.read()
        try:
            data = json.loads(body_bytes)
            if not isinstance(data, dict):
                data = {"error": str(data)}
        except Exception:
            data = {"error": e.reason}
        data["status"] = e.code
        quota = _quota_from_headers(e.headers)
        if quota:
            data["_quota"] = quota
        return data
    except Exception as e:
        return {"error": str(e)}


def _carry_quota(response, raw_result):
    """Copy _quota from a raw _api() result into a handler's reshaped
    response, so quota visibility survives even when a handler picks
    specific fields rather than returning the raw result directly."""
    if isinstance(raw_result, dict) and "_quota" in raw_result:
        response["_quota"] = raw_result["_quota"]
    return response


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
        "name": "list_recent_claims",
        "description": (
            "List recent claims from Verum Signal's corpus, optionally filtered by "
            "outlet, verdict, or claim origin. This does NOT perform text search — "
            "there is no keyword or topic search over the claim corpus. It returns "
            "claims in recency order, most recent first, matching whatever filters "
            "are given. Use get_outlet_score for a specific outlet's overall "
            "reliability, or get_debate_verdicts for claims from a specific debate."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "verdict": {
                    "type": "string",
                    "description": "Filter by verdict: supported, disputed, overstated, not_supported, etc.",
                    "enum": ["supported","plausible","corroborated","overstated","disputed","not_supported","not_verifiable","opinion"]
                },
                "outlet": {
                    "type": "string",
                    "description": "Filter by outlet domain, e.g. foxnews.com"
                },
                "claim_origin": {
                    "type": "string",
                    "description": "Filter by how the claim originated.",
                    "enum": ["outlet_claim", "attributed_claim"]
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of results (1-50, default 10)",
                    "default": 10
                }
            },
            "required": []
        }
    },
    {
        "name": "get_debate_verdicts",
        "description": (
            "Get ALL verified claims from a political debate. Returns claims with "
            "speaker attribution, verdicts, and evidence. Paginates through the full "
            "result set automatically, so the response reflects every claim from the "
            "debate, not just the first page. Use this to fact-check debate statements."
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
    response = {
        "domain": domain,
        "score": result.get("score"),
        "tier": result.get("tier"),
        "scoreable_claims": result.get("total_evaluated_claims"),
        "verdict_counts": result.get("verdict_counts", {}),
        "methodology_version": result.get("methodology_version"),
        "leaderboard_url": f"https://verumsignal.com/outlet/{domain}",
    }
    return _carry_quota(response, result)


def handle_list_recent_claims(args):
    params = {"limit": min(args.get("limit", 10), 50)}
    if args.get("verdict"):
        params["verdict"] = args["verdict"]
    if args.get("outlet"):
        params["outlet"] = args["outlet"]
    if args.get("claim_origin"):
        params["claim_origin"] = args["claim_origin"]
    result = _api("/v1/claims", params)
    if "error" in result:
        return result
    claims = result.get("data", [])
    response = {
        "count": len(claims),
        "claims": [{
            "id": c.get("id"),
            "claim_text": c.get("claim_text"),
            "verdict": c.get("verdict"),
            "confidence_score": c.get("confidence_score"),
            "outlet": c.get("outlet"),
            "methodology_version": c.get("methodology_version"),
        } for c in claims]
    }
    return _carry_quota(response, result)


def handle_get_debate_verdicts(args):
    slug = args.get("slug", "").lower().strip()
    if not slug:
        return {"error": "slug required"}

    all_claims = []
    cursor = 0
    last_result = None
    MAX_PAGES = 20  # safety cap: 20 pages at the API's own default page size
                     # is far beyond any real debate seen so far (largest is 104
                     # claims); this exists to bound a pathological case, not to
                     # normally trigger.
    truncated = False
    for _ in range(MAX_PAGES):
        result = _api(f"/v1/debates/{slug}/claims", {"cursor": cursor})
        last_result = result
        if "error" in result:
            return result
        page_claims = result.get("data", [])
        all_claims.extend(page_claims)
        pagination = result.get("pagination", {})
        if not pagination.get("has_more"):
            break
        cursor = pagination.get("next_cursor")
        if cursor is None:
            break
    else:
        # Loop exhausted MAX_PAGES without has_more going false.
        truncated = True

    response = {
        "slug": slug,
        "count": len(all_claims),
        "claims": [{
            "claim_text": c.get("claim_text"),
            "speaker": c.get("speaker"),
            "verdict": c.get("verdict"),
            "confidence_score": c.get("confidence_score"),
            "is_provisional": c.get("is_provisional"),
        } for c in all_claims]
    }
    if truncated:
        response["truncated"] = True
        response["note"] = (
            f"Stopped after {MAX_PAGES} pages as a safety limit -- this debate "
            f"has more claims than were retrieved. This should be rare."
        )
    return _carry_quota(response, last_result)


def handle_list_debates(args):
    result = _api("/v1/debates")
    if "error" in result:
        return result
    response = {"debates": result.get("data", [])}
    return _carry_quota(response, result)


def handle_get_api_status(args):
    return _api("/v1/meta")


HANDLERS = {
    "get_outlet_score":     handle_get_outlet_score,
    "list_recent_claims":   handle_list_recent_claims,
    "get_debate_verdicts":  handle_get_debate_verdicts,
    "list_debates":         handle_list_debates,
    "get_api_status":       handle_get_api_status,
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
            "serverInfo":{"name":"verum-signal","version":"0.1.0"}
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
