"""The catalog of well-known public MCP servers `omega connections` can offer,
plus a read-only view of whatever Claude Code already has configured.

`verified=True` means the url/command was confirmed against the vendor's own
current docs (or the official modelcontextprotocol/servers repo). Where that
couldn't be confirmed, the entry keeps its best-known value with
`verified=False` and a `docs` link so the user can check before connecting --
never a guessed endpoint standing in for a real one."""

from dataclasses import dataclass, field
from typing import Any, Literal

from . import config, mcp

Transport = Literal["remote", "stdio"]
Auth = Literal["oauth", "api_key", "none"]
Category = Literal["search & web", "code & dev", "cloud & infra", "data",
                   "observability", "work & pm", "payments & commerce", "utilities", "design"]


@dataclass(frozen=True)
class Integration:
    key: str
    name: str
    category: Category
    blurb: str
    transport: Transport
    url: str | None
    command: list[str] | None
    auth: Auth
    verified: bool
    env: tuple[str, ...] = field(default_factory=tuple)
    docs: str = ""


def _remote(key: str, name: str, category: Category, blurb: str, url: str | None, auth: Auth,
           verified: bool, env: tuple[str, ...] = (), docs: str = "") -> Integration:
    return Integration(key, name, category, blurb, "remote", url, None, auth, verified, env, docs)


def _stdio(key: str, name: str, category: Category, blurb: str, command: list[str], auth: Auth,
          verified: bool, env: tuple[str, ...] = (), docs: str = "") -> Integration:
    return Integration(key, name, category, blurb, "stdio", None, command, auth, verified, env, docs)


CATALOG: dict[str, Integration] = {
    i.key: i for i in [
        # -- search & web --------------------------------------------------
        _remote("perplexity", "Perplexity", "search & web", "Web-grounded answers and research.",
               "https://api.perplexity.ai/mcp", "api_key", True,
               env=("PERPLEXITY_API_KEY",),
               docs="https://docs.perplexity.ai/docs/getting-started/integrations/mcp-server"),
        _stdio("exa", "Exa", "search & web", "AI-native web search.",
              ["npx", "-y", "exa-mcp-server"], "api_key", True,
              env=("EXA_API_KEY",),
              docs="https://docs.exa.ai/reference/exa-mcp"),
        _stdio("brave-search", "Brave Search", "search & web", "Web, local, image, video and news search.",
              ["npx", "-y", "@brave/brave-search-mcp-server"], "api_key", True,
              env=("BRAVE_API_KEY",),
              docs="https://www.npmjs.com/package/@brave/brave-search-mcp-server"),
        _stdio("tavily", "Tavily", "search & web", "Search and extraction tuned for agents.",
              ["npx", "-y", "tavily-mcp"], "api_key", True,
              env=("TAVILY_API_KEY",),
              docs="https://docs.tavily.com/documentation/mcp"),
        _stdio("firecrawl", "Firecrawl", "search & web", "Scrape, crawl and extract structured web data.",
              ["npx", "-y", "firecrawl-mcp"], "api_key", True,
              env=("FIRECRAWL_API_KEY",),
              docs="https://docs.firecrawl.dev/mcp-server/local"),
        _stdio("fetch", "Fetch", "search & web", "Fetch a URL and convert it to markdown.",
              ["uvx", "mcp-server-fetch"], "none", True,
              docs="https://github.com/modelcontextprotocol/servers/tree/main/src/fetch"),

        # -- code & dev -------------------------------------------------------
        _remote("github", "GitHub", "code & dev", "Repos, issues, PRs and code search.",
               "https://api.githubcopilot.com/mcp/", "oauth", True,
               docs="https://github.com/github/github-mcp-server"),
        _remote("gitlab", "GitLab", "code & dev", "Projects, issues and merge requests (needs Duo enabled).",
               "https://gitlab.com/api/v4/mcp", "oauth", True,
               docs="https://docs.gitlab.com/user/model_context_protocol/mcp_server/"),
        _remote("context7", "Context7", "code & dev", "Up-to-date library and framework docs.",
               "https://mcp.context7.com/mcp", "api_key", True,
               env=("CONTEXT7_API_KEY",),
               docs="https://github.com/upstash/context7"),
        _stdio("chrome-devtools", "Chrome DevTools", "code & dev", "Drive and inspect a live Chrome instance.",
              ["npx", "-y", "chrome-devtools-mcp@latest"], "none", True,
              docs="https://github.com/ChromeDevTools/chrome-devtools-mcp"),
        _stdio("playwright", "Playwright", "code & dev", "Drive a real browser: navigate, click, screenshot.",
              ["npx", "-y", "@playwright/mcp@latest"], "none", True,
              docs="https://github.com/microsoft/playwright-mcp"),
        _stdio("docker", "Docker MCP Gateway", "code & dev", "Run containerized MCP servers from Docker's catalog.",
              ["docker", "mcp", "gateway", "run"], "none", True,
              docs="https://docs.docker.com/ai/mcp-catalog-and-toolkit/mcp-gateway/"),
        _remote("hugging-face", "Hugging Face", "code & dev", "Search models, datasets, Spaces and papers.",
               "https://huggingface.co/mcp", "oauth", True,
               env=("HF_TOKEN",),
               docs="https://huggingface.co/docs/hub/hf-mcp-server"),

        # -- cloud & infra ----------------------------------------------------
        _remote("cloudflare-docs", "Cloudflare Docs", "cloud & infra", "Search Cloudflare's documentation.",
               "https://docs.mcp.cloudflare.com/mcp", "none", True,
               docs="https://developers.cloudflare.com/agents/model-context-protocol/"),
        _remote("cloudflare-bindings", "Cloudflare Bindings", "cloud & infra",
               "Build Workers with storage, AI and compute bindings.",
               "https://bindings.mcp.cloudflare.com/mcp", "oauth", True,
               docs="https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/"),
        _remote("cloudflare-observability", "Cloudflare Observability", "cloud & infra",
               "Workers logs, traffic trends and analytics.",
               "https://observability.mcp.cloudflare.com/sse", "oauth", True,
               docs="https://developers.cloudflare.com/agents/model-context-protocol/cloudflare/servers-for-cloudflare/"),
        _remote("vercel", "Vercel", "cloud & infra", "Deployments, projects and domains.",
               "https://mcp.vercel.com", "oauth", True,
               docs="https://vercel.com/docs/mcp/vercel-mcp"),
        _remote("supabase", "Supabase", "cloud & infra", "Postgres, auth and storage projects.",
               "https://mcp.supabase.com/mcp", "oauth", True,
               docs="https://supabase.com/docs/guides/getting-started/mcp"),
        _stdio("railway", "Railway", "cloud & infra", "Manage Railway projects, services and deploys.",
              ["railway", "mcp"], "none", True,
              docs="https://docs.railway.com/cli/mcp"),
        _remote("neon", "Neon", "cloud & infra", "Serverless Postgres: branches, SQL and docs.",
               "https://mcp.neon.tech/mcp", "oauth", True,
               docs="https://neon.com/docs/ai/neon-mcp-server"),
        _stdio("aws-docs", "AWS Documentation", "cloud & infra", "Search and read AWS documentation.",
              ["uvx", "awslabs.aws-documentation-mcp-server@latest"], "none", True,
              docs="https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server"),

        # -- data ---------------------------------------------------------
        _stdio("postgres", "Postgres", "data", "Query and inspect a Postgres database.",
              ["postgres-mcp"], "api_key", True,
              env=("DATABASE_URL",),
              docs="https://github.com/crystaldba/postgres-mcp"),
        _stdio("redis", "Redis", "data", "Manage and search data in Redis.",
              ["uvx", "--from", "redis-mcp-server@latest", "redis-mcp-server"], "api_key", True,
              env=("REDIS_URL",),
              docs="https://redis.io/docs/latest/integrate/redis-mcp/install/"),
        _stdio("bigquery", "BigQuery", "data",
              "Inspect schemas and run queries (needs the toolbox binary + gcloud ADC).",
              ["toolbox", "--prebuilt", "bigquery"], "none", True,
              docs="https://github.com/googleapis/mcp-toolbox"),
        _stdio("airtable", "Airtable", "data", "Read and write Airtable bases, tables and records.",
              ["npx", "-y", "airtable-mcp-server"], "api_key", False,
              env=("AIRTABLE_API_KEY",),
              docs="https://www.npmjs.com/package/airtable-mcp-server"),

        # -- observability --------------------------------------------------
        _remote("sentry", "Sentry", "observability", "Errors, issues and traces.",
               "https://mcp.sentry.dev/mcp", "oauth", True,
               docs="https://docs.sentry.io/product/sentry-mcp/"),
        _remote("posthog", "PostHog", "observability", "Product analytics, insights and feature flags.",
               "https://mcp.posthog.com/mcp", "api_key", True,
               env=("POSTHOG_API_KEY",),
               docs="https://posthog.com/docs/model-context-protocol"),
        _remote("datadog", "Datadog", "observability", "APM, logs, metrics, monitors and dashboards.",
               None, "oauth", False,
               docs="https://docs.datadoghq.com/mcp_server/setup/"),
        _stdio("grafana", "Grafana", "observability", "Dashboards, alerts and datasources (via Docker).",
              ["docker", "run", "--rm", "-i", "-e", "GRAFANA_URL", "-e", "GRAFANA_SERVICE_ACCOUNT_TOKEN",
               "grafana/mcp-grafana", "-t", "stdio"], "api_key", True,
              env=("GRAFANA_URL", "GRAFANA_SERVICE_ACCOUNT_TOKEN"),
              docs="https://grafana.com/docs/grafana/latest/developer-resources/mcp/set-up/"),

        # -- work & pm --------------------------------------------------------
        _remote("linear", "Linear", "work & pm", "Issues, projects and cycles.",
               "https://mcp.linear.app/mcp", "oauth", True,
               docs="https://linear.app/developers/mcp"),
        _remote("notion", "Notion", "work & pm", "Pages, databases and search.",
               "https://mcp.notion.com/mcp", "oauth", True,
               docs="https://developers.notion.com/docs/get-started-with-mcp"),
        _stdio("slack", "Slack", "work & pm", "Read and post to channels and threads.",
              ["npx", "-y", "@modelcontextprotocol/server-slack"], "api_key", True,
              env=("SLACK_BOT_TOKEN", "SLACK_TEAM_ID"),
              docs="https://github.com/modelcontextprotocol/servers/tree/main/src/slack"),
        _remote("atlassian", "Atlassian", "work & pm", "Jira, Confluence, Bitbucket and Compass.",
               "https://mcp.atlassian.com/v1/sse", "oauth", True,
               docs="https://support.atlassian.com/atlassian-rovo-mcp-server/docs/use-atlassian-rovo-mcp-server/"),
        _remote("asana", "Asana", "work & pm", "Tasks, projects and workload across the Work Graph.",
               "https://mcp.asana.com/v2/mcp", "oauth", True,
               docs="https://developers.asana.com/docs/using-asanas-mcp-server"),
        _remote("zapier", "Zapier", "work & pm", "Trigger actions across 8,000+ connected apps.",
               None, "api_key", False,
               docs="https://docs.zapier.com/mcp"),
        _stdio("google-workspace", "Google Workspace", "work & pm", "Gmail, Calendar, Drive, Docs and Sheets.",
              ["uvx", "workspace-mcp"], "oauth", False,
              env=("GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET"),
              docs="https://github.com/taylorwilsdon/google_workspace_mcp"),
        _remote("intercom", "Intercom", "work & pm", "Conversations, contacts and Help Center articles.",
               "https://mcp.intercom.com/mcp", "oauth", True,
               docs="https://developers.intercom.com/docs/guides/mcp"),
        _remote("hubspot", "HubSpot", "work & pm", "CRM contacts, deals and marketing objects.",
               "https://mcp.hubspot.com", "oauth", True,
               docs="https://developers.hubspot.com/docs/apps/developer-platform/build-apps/integrate-with-the-remote-hubspot-mcp-server"),

        # -- payments & commerce ----------------------------------------------
        _remote("stripe", "Stripe", "payments & commerce", "Payments, customers and subscriptions.",
               "https://mcp.stripe.com", "api_key", True,
               env=("STRIPE_API_KEY",),
               docs="https://docs.stripe.com/mcp"),
        _remote("paypal", "PayPal", "payments & commerce", "Invoices, payments, refunds and disputes.",
               "https://mcp.paypal.com/mcp", "oauth", True,
               docs="https://developer.paypal.com/ai-tools/mcp-server"),
        _stdio("shopify", "Shopify Dev", "payments & commerce", "API docs, GraphQL schemas and code validation.",
              ["npx", "-y", "@shopify/dev-mcp@latest"], "none", True,
              docs="https://shopify.dev/changelog/mcp-server-for-the-shopify-dev-assistant"),

        # -- design -----------------------------------------------------------
        _remote("figma", "Figma", "design", "Design files, components and dev-mode inspection.",
               "https://mcp.figma.com/mcp", "oauth", False,
               docs="https://www.figma.com/developers/mcp"),

        # -- utilities --------------------------------------------------------
        _stdio("filesystem", "Filesystem", "utilities", "Read and write files outside the working directory.",
              ["npx", "-y", "@modelcontextprotocol/server-filesystem", "<cwd>"], "none", True,
              docs="https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem"),
        _stdio("memory", "Memory", "utilities", "A knowledge-graph scratchpad for a session.",
              ["npx", "-y", "@modelcontextprotocol/server-memory"], "none", True,
              docs="https://github.com/modelcontextprotocol/servers/tree/main/src/memory"),
        _stdio("sequential-thinking", "Sequential Thinking", "utilities", "Step-by-step reflective problem solving.",
              ["npx", "-y", "@modelcontextprotocol/server-sequential-thinking"], "none", True,
              docs="https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking"),
        _stdio("time", "Time", "utilities", "Timezone lookups and conversions.",
              ["uvx", "mcp-server-time"], "none", True,
              docs="https://github.com/modelcontextprotocol/servers/tree/main/src/time"),
    ]
}


def _sanitized(cfg: dict[str, Any]) -> dict[str, Any]:
    """Never surface secret values -- only that an env var or header is set."""
    out: dict[str, Any] = {}
    if "command" in cfg:
        out["transport"] = "stdio"
        out["command"] = cfg["command"]
        # A manually-configured remote proxy stores its token inline
        # (`--header Authorization:Bearer ...`), not in `env` -- redact it too.
        args = list(cfg.get("args", []))
        for idx, a in enumerate(args):
            if a in ("--header", "-H") and idx + 1 < len(args):
                args[idx + 1] = "***"
        out["args"] = args
    else:
        out["transport"] = "remote"
        out["url"] = cfg.get("url") or cfg.get("serverUrl") or ""
    if cfg.get("env"):
        out["env"] = sorted(cfg["env"])
    if cfg.get("headers"):
        out["headers"] = sorted(cfg["headers"])
    return out


def imported_from_claude_code() -> dict[str, dict[str, Any]]:
    """Servers omega's own mcp.discover() picked up from ~/.claude.json and
    installed plugins, formatted for display -- names and shapes only, no
    tokens or header values."""
    return {name: _sanitized(cfg) for name, cfg in mcp.discover(include_omega=False).items()}


def overview() -> list[dict[str, Any]]:
    """One row per server: what omega has configured, what the catalog offers
    that isn't configured yet, and what Claude Code has that isn't either --
    the shared data behind `omega connections` and the setup page's Tools step."""
    configured = mcp.status()
    raw = config.mcp_config()
    rows: list[dict[str, Any]] = []
    for name, st in sorted(configured.items()):
        cat = CATALOG.get(raw.get(name, {}).get("catalog", ""))
        rows.append({
            "name": name, "state": st.state, "tools": st.tools,
            "auth": cat.auth if cat else None, "source": "config",
            "last_used": st.last_used, "category": cat.category if cat else None,
            "verified": cat.verified if cat else None, "error": st.error,
        })

    known = set(configured)
    claude_only = {n for n in imported_from_claude_code() if n not in known}
    for key, i in sorted(CATALOG.items()):
        if key in known or key in claude_only:
            continue
        rows.append({"name": key, "state": "available", "tools": 0, "auth": i.auth,
                    "source": "catalog", "last_used": None, "category": i.category,
                    "verified": i.verified, "error": None})
    for name in sorted(claude_only):
        rows.append({"name": name, "state": "found", "tools": 0, "auth": None,
                    "source": "claude-code", "last_used": None, "category": None,
                    "verified": None, "error": None})
    return rows
