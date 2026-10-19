"""
Talk-to-TikTok MCP Server (remote).

Streamable-HTTP TikTok Ads MCP for Claude custom connectors, deployed on Railway.

Auth model: AUTHLESS (Parker-style).
  The server advertises no OAuth. Claude connects to the URL with no login step.
  An OPTIONAL shared token embedded in the connector URL (?access_token=...) or an
  Authorization: Bearer header is validated by this server itself (see __main__.py).
  Set MCP_AUTH_TOKEN to enable the gate; leave it empty to run fully open.

Credentials: a single TIKTOK_ACCESS_TOKEN (shared for the whole team) is read from
the environment. The advertiser account is chosen per-call (advertiser_id argument)
or via the TIKTOK_ADVERTISER_ID default; tiktok_ads_switch_ad_account sets a
process-wide default for convenience.
"""
import logging
import os
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from .tiktok_client import TikTokAdsClient
from .tools import (
    CampaignTools,
    CreativeTools,
    PerformanceTools,
    AudienceTools,
    ReportingTools,
    ManagementTools,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("talk-to-tiktok")

# ── Configuration ────────────────────────────────────────────────────────────
SERVER_URL = os.environ.get("SERVER_URL", "").rstrip("/")
ACCESS_TOKEN = os.environ.get("TIKTOK_ACCESS_TOKEN", "")
AUTH_TOKEN = os.environ.get("MCP_AUTH_TOKEN", "")  # optional URL/bearer gate (see __main__.py)

# Process-wide default advertiser. Every tool also accepts an explicit advertiser_id.
_default_advertiser = os.environ.get("TIKTOK_ADVERTISER_ID", "") or None

if not ACCESS_TOKEN:
    logger.warning("TIKTOK_ACCESS_TOKEN not set — tools will return an error until it is configured.")

# ── Tool classification (drives Claude's permission management) ───────────────
# readOnlyHint  -> safe data reads; Claude can allow these broadly.
# WRITE         -> creates objects in the TikTok ad account; gate these.
# openWorldHint -> every tool talks to the external TikTok Business API.
READONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
# DESTRUCTIVE (delete/irreversible) — none exist in the current TikTok toolset.

# Allow the deployment host(s) through the MCP SDK's DNS-rebinding protection,
# otherwise every POST /mcp returns 421 "Invalid Host header". We collect hosts
# from several sources so a missing scheme or wrong SERVER_URL can't break it:
#   - SERVER_URL           (tolerates a missing scheme, e.g. "foo.up.railway.app")
#   - RAILWAY_PUBLIC_DOMAIN (auto-injected by Railway when a domain is generated)
#   - MCP_ALLOWED_HOSTS     (optional comma-separated manual override / escape hatch)
def _derive_allowed_hosts() -> list[str]:
    candidates: list[str] = []
    if SERVER_URL:
        _u = SERVER_URL if "//" in SERVER_URL else "https://" + SERVER_URL
        netloc = urlparse(_u).netloc
        if netloc:
            candidates.append(netloc)
    rpd = os.environ.get("RAILWAY_PUBLIC_DOMAIN", "").strip()
    if rpd:
        candidates.append(rpd)
    for h in os.environ.get("MCP_ALLOWED_HOSTS", "").split(","):
        if h.strip():
            candidates.append(h.strip())
    seen, out = set(), []
    for h in candidates:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


_allowed_hosts = _derive_allowed_hosts()
if _allowed_hosts:
    _hosts = [x for h in _allowed_hosts for x in (h, f"{h}:*")]
    _origins = [x for h in _allowed_hosts for x in (f"https://{h}", f"http://{h}")]
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_hosts,
        allowed_origins=_origins,
    )
    logger.info("DNS-rebinding protection ON — allowed hosts: %s", _allowed_hosts)
else:
    # No host to pin (local/dev) — disable protection so it still runs.
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)
    logger.warning("No SERVER_URL / RAILWAY_PUBLIC_DOMAIN — DNS-rebinding protection OFF")

mcp = FastMCP(
    "Talk-to-TikTok",
    instructions=(
        "Talk-to-TikTok connects TikTok Ads (TikTok Business API) to Claude. "
        "Read tools retrieve campaigns, ad groups, performance, creatives, audiences, "
        "and reports. Write tools create and edit campaigns, ad groups and ads, and pause/resume them. "
        "Select the advertiser with tiktok_ads_switch_ad_account, or pass advertiser_id per call."
    ),
    transport_security=_transport_security,
)

logger.info(
    "Talk-to-TikTok configured — server_url=%s auth_gate=%s default_advertiser=%s",
    SERVER_URL or "(unset)", bool(AUTH_TOKEN), _default_advertiser or "(none)",
)


# ── Helpers ──────────────────────────────────────────────────────────────────
def _resolve_advertiser(advertiser_id: str | None) -> str | None:
    """Explicit arg wins, then the process-wide default, then the env default."""
    return advertiser_id or _default_advertiser


def _client(advertiser_id: str | None) -> TikTokAdsClient:
    return TikTokAdsClient(access_token=ACCESS_TOKEN, advertiser_id=advertiser_id)


def _missing_token() -> dict:
    return {
        "success": False,
        "error": "TIKTOK_ACCESS_TOKEN is not configured on the server.",
        "message": "Set TIKTOK_ACCESS_TOKEN in the Railway environment.",
    }


def _missing_advertiser() -> dict:
    return {
        "success": False,
        "error": "No advertiser selected.",
        "message": (
            "Pass advertiser_id, set TIKTOK_ADVERTISER_ID on the server, "
            "or call tiktok_ads_switch_ad_account first."
        ),
    }


# ============================================================================
# READ-ONLY TOOLS
# ============================================================================

@mcp.tool(annotations=READONLY)
async def tiktok_ads_auth_status() -> dict:
    """Check server authentication status: whether a TikTok access token is
    configured and which advertiser account is currently the default."""
    return {
        "success": True,
        "data": {
            "access_token_configured": bool(ACCESS_TOKEN),
            "default_advertiser_id": _resolve_advertiser(None),
            "auth_gate_enabled": bool(AUTH_TOKEN),
            "message": (
                "Ready. Select an advertiser with tiktok_ads_switch_ad_account "
                "or pass advertiser_id per call."
                if ACCESS_TOKEN else
                "TIKTOK_ACCESS_TOKEN is not configured on the server."
            ),
        },
    }


@mcp.tool(annotations=READONLY)
async def tiktok_ads_switch_ad_account(advertiser_id: str) -> dict:
    """Set the process-wide default advertiser account for subsequent tool calls.

    NOTE: this default is shared across everyone using this server. When multiple
    people use different accounts at once, prefer passing advertiser_id explicitly
    to each tool instead of relying on this default.

    Args:
        advertiser_id: The TikTok advertiser ID to use as the default.
    """
    global _default_advertiser
    _default_advertiser = advertiser_id
    logger.info("Default advertiser set to %s", advertiser_id)
    return {
        "success": True,
        "data": {"advertiser_id": advertiser_id, "message": f"Default advertiser set to {advertiser_id}."},
    }


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_campaigns(
    status: str | None = None,
    limit: int = 10,
    advertiser_id: str | None = None,
) -> dict:
    """Retrieve campaigns for the advertiser account.

    Args:
        status: Filter by status (e.g. STATUS_ALL, STATUS_NOT_DELETE, STATUS_DELIVERY_OK, STATUS_DISABLE).
        limit: Maximum number of campaigns to return.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await CampaignTools(client).get_campaigns(status=status, limit=limit)
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_campaign_details(campaign_id: str, advertiser_id: str | None = None) -> dict:
    """Get detailed information about a specific campaign.

    Args:
        campaign_id: The campaign ID to retrieve details for.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await CampaignTools(client).get_campaign_details(campaign_id)
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_adgroups(
    campaign_id: str,
    status: str | None = None,
    limit: int = 10,
    advertiser_id: str | None = None,
) -> dict:
    """Retrieve ad groups for a campaign.

    Args:
        campaign_id: Campaign ID to get ad groups for.
        status: Filter by status (e.g. STATUS_ALL, STATUS_NOT_DELETE, STATUS_DELIVERY_OK).
        limit: Maximum number of ad groups to return.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await CampaignTools(client).get_adgroups(campaign_id=campaign_id, status=status, limit=limit)
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_campaign_performance(
    campaign_ids: list[str],
    date_range: str = "last_7_days",
    metrics: list[str] | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """Get performance metrics for campaigns.

    Args:
        campaign_ids: List of campaign IDs to analyze.
        date_range: One of today, yesterday, last_7_days, last_14_days, last_30_days.
        metrics: Metrics to include (e.g. spend, impressions, clicks, ctr, cpc, cpm, conversions).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await PerformanceTools(client).get_campaign_performance(
            campaign_ids=campaign_ids, date_range=date_range, metrics=metrics
        )
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_adgroup_performance(
    adgroup_ids: list[str],
    date_range: str = "last_7_days",
    metrics: list[str] | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """Get performance metrics for ad groups.

    Args:
        adgroup_ids: List of ad group IDs to analyze.
        date_range: One of today, yesterday, last_7_days, last_14_days, last_30_days.
        metrics: Metrics to include (e.g. spend, impressions, clicks, ctr, cpc, cpm, conversions).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await PerformanceTools(client).get_adgroup_performance(
            adgroup_ids=adgroup_ids, date_range=date_range, metrics=metrics
        )
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_ad_creatives(
    limit: int = 10,
    creative_type: str | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """List ad creatives for the advertiser.

    Args:
        limit: Maximum number of creatives to return.
        creative_type: Optional filter (IMAGE, VIDEO, CAROUSEL).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await CreativeTools(client).get_ad_creatives(limit=limit, creative_type=creative_type)
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_custom_audiences(limit: int = 10, advertiser_id: str | None = None) -> dict:
    """List custom audiences for the advertiser.

    Args:
        limit: Maximum number of audiences to return.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await AudienceTools(client).get_custom_audiences(limit=limit)
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_targeting_options(
    type: str,
    country_code: str | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """Get available targeting options for campaigns.

    Args:
        type: One of INTEREST, BEHAVIOR, DEMOGRAPHICS, LOCATION.
        country_code: Country code for location-specific options.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await AudienceTools(client).get_targeting_options(type=type, country_code=country_code)
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_available_metrics() -> dict:
    """Get the catalog of available reporting metrics, organized by category.
    Does not call the TikTok API."""
    client = _client(None)
    try:
        return await ReportingTools(client).get_available_metrics()
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_generate_report(
    report_type: str,
    dimensions: list[str],
    metrics: list[str],
    start_date: str,
    end_date: str,
    filtering: list[dict] | dict | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """Run a custom performance report. Normally async: returns a task_id to poll
    with tiktok_ads_get_report_status and fetch with tiktok_ads_download_report.
    If async reports aren't enabled for the app, runs synchronously and returns rows inline.

    Args:
        report_type: BASIC, AUDIENCE, PLACEMENT, or DPA.
        dimensions: e.g. campaign_id, adgroup_id, ad_id, stat_time_day.
        metrics: e.g. impressions, clicks, spend, ctr, cpm, cpc, conversion.
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD.
        filtering: Optional list of {field_name, filter_type, filter_value}, e.g.
            [{"field_name": "campaign_ids", "filter_type": "IN", "filter_value": "[\\"123\\"]"}].
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await ReportingTools(client).generate_report(
            report_type=report_type,
            dimensions=dimensions,
            metrics=metrics,
            date_range={"start_date": start_date, "end_date": end_date},
            filtering=filtering,
        )
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_report_status(task_id: str, advertiser_id: str | None = None) -> dict:
    """Check the status of an async report task.

    Args:
        task_id: The report task ID from tiktok_ads_generate_report.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await ReportingTools(client).get_report_status(task_id)
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_download_report(task_id: str, advertiser_id: str | None = None) -> dict:
    """Download a completed async report (returns up to the first 100 rows as preview).

    Args:
        task_id: The report task ID (must be in SUCCESS state).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await ReportingTools(client).download_report(task_id)
    finally:
        await client.close()


# ============================================================================
# WRITE TOOLS  (create objects in the TikTok ad account)
# ============================================================================

@mcp.tool(annotations=WRITE)
async def tiktok_ads_create_campaign(
    name: str,
    objective: str,
    budget: float,
    special_industries: list[str] | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """Create a new advertising campaign (daily budget).

    Args:
        name: Campaign name.
        objective: Campaign objective (e.g. REACH, TRAFFIC, APP_INSTALL, CONVERSIONS).
        budget: Daily budget in the advertiser's currency.
        special_industries: Special industry categories, if applicable.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await CampaignTools(client).create_campaign(
            name=name, objective=objective, budget=budget, special_industries=special_industries
        )
    finally:
        await client.close()


@mcp.tool(annotations=WRITE)
async def tiktok_ads_create_adgroup(
    campaign_id: str,
    name: str,
    placement_type: str,
    budget: float,
    bid_type: str = "BID_TYPE_NO_BID",
    advertiser_id: str | None = None,
) -> dict:
    """Create a new ad group within a campaign (daily budget).

    Args:
        campaign_id: Parent campaign ID.
        name: Ad group name.
        placement_type: PLACEMENT_TYPE_AUTOMATIC or PLACEMENT_TYPE_NORMAL.
        budget: Daily budget for the ad group.
        bid_type: BID_TYPE_NO_BID or BID_TYPE_CUSTOM.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await CampaignTools(client).create_adgroup(
            campaign_id=campaign_id, name=name, placement_type=placement_type,
            budget=budget, bid_type=bid_type,
        )
    finally:
        await client.close()


# ============================================================================
# MANAGEMENT TOOLS  (ads, review info, changelog, advertiser info, edits)
# ============================================================================

async def _manage(advertiser_id: str | None, call):
    """Run call(ManagementTools) for the resolved advertiser, with shared guards."""
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await call(ManagementTools(client))
    finally:
        await client.close()


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_ads(
    adgroup_id: str | None = None,
    campaign_id: str | None = None,
    ad_ids: list[str] | None = None,
    status: str | None = None,
    limit: int = 10,
    advertiser_id: str | None = None,
) -> dict:
    """List ads (with creative details) filtered by ad group, campaign or ad IDs.

    Args:
        adgroup_id: Only ads in this ad group.
        campaign_id: Only ads in this campaign.
        ad_ids: Only these ad IDs.
        status: Optional primary_status filter (e.g. STATUS_ALL, STATUS_DELIVERY_OK).
        limit: Maximum number of ads to return.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.get_ads(adgroup_id, campaign_id, ad_ids, status, limit))


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_ad_performance(
    ad_ids: list[str],
    start_date: str,
    end_date: str,
    metrics: list[str] | None = None,
    dimensions: list[str] | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """Get ad-level performance metrics.

    Args:
        ad_ids: Ad IDs to report on.
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD.
        metrics: Defaults to spend, impressions, clicks, ctr, cpc, cpm, conversion, cost_per_conversion.
        dimensions: Defaults to ["ad_id"]; use ["ad_id", "stat_time_day"] for a daily breakdown.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    metrics = metrics or ["spend", "impressions", "clicks", "ctr", "cpc", "cpm", "conversion", "cost_per_conversion"]
    dimensions = dimensions or ["ad_id"]
    return await _manage(advertiser_id, lambda m: m.get_ad_performance(
        "AUCTION_AD", ad_ids, metrics, start_date, end_date, dimensions))


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_ad_review_info(ad_ids: list[str], advertiser_id: str | None = None) -> dict:
    """Get review/approval status and rejection reasons for ads.

    Args:
        ad_ids: Ad IDs to check.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.get_ad_review_info(ad_ids))


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_adgroup_review_info(adgroup_ids: list[str], advertiser_id: str | None = None) -> dict:
    """Get review/approval status and rejection reasons for ad groups.

    Args:
        adgroup_ids: Ad group IDs to check.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.get_adgroup_review_info(adgroup_ids))


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_changelog(
    module: str,
    start_date: str | None = None,
    end_date: str | None = None,
    filtering: dict | None = None,
    limit: int = 20,
    bc_id: str | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """Get the account change log (who changed what, and when).

    Args:
        module: Object type, e.g. CAMPAIGN, ADGROUP, AD.
        start_date: Optional start (YYYY-MM-DD).
        end_date: Optional end (YYYY-MM-DD).
        filtering: Optional TikTok changelog filtering object.
        limit: Maximum number of entries.
        bc_id: Business Center ID (defaults to the advertiser's owner_bc_id).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.get_changelog(module, start_date, end_date, filtering, limit, bc_id=bc_id))


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_advertiser_info(fields: list[str] | None = None, advertiser_id: str | None = None) -> dict:
    """Get advertiser account details (name, currency, timezone, status, etc.).

    Args:
        fields: Optional list of fields to return.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.get_advertiser_info(fields))


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_advertiser_balance(bc_id: str | None = None, advertiser_id: str | None = None) -> dict:
    """Get the advertiser account balance.

    Args:
        bc_id: Business Center ID (defaults to the advertiser's owner_bc_id).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.get_advertiser_balance(bc_id))


@mcp.tool(annotations=READONLY)
async def tiktok_ads_get_saved_audiences(limit: int = 10, advertiser_id: str | None = None) -> dict:
    """List saved audiences (reusable targeting presets).

    Args:
        limit: Maximum number of saved audiences to return.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.get_saved_audiences(limit))


@mcp.tool(annotations=READONLY)
async def tiktok_ads_estimate_audience_size(targeting: dict, advertiser_id: str | None = None) -> dict:
    """Estimate audience size for a targeting spec (read-only; creates nothing).

    Args:
        targeting: TikTok /ad/audience_size/estimate/ body fields. Required: objective_type,
            optimization_goal, promotion_type (e.g. WEBSITE_OR_DISPLAY). Also e.g. placements,
            location_ids, age_groups, gender, interest_category_ids. (The field is
            optimization_goal, not optimize_goal.)
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.estimate_audience_size(targeting))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_update_campaign(campaign_id: str, fields: dict, advertiser_id: str | None = None) -> dict:
    """Update a campaign (rename, change budget, etc.).

    Args:
        campaign_id: Campaign to update.
        fields: TikTok /campaign/update/ fields, e.g. {"campaign_name": "...", "budget": 100}.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.update_campaign(campaign_id, fields))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_update_campaign_status(
    campaign_ids: list[str], operation_status: str, advertiser_id: str | None = None
) -> dict:
    """Pause or resume campaigns. Deleting is not supported.

    Args:
        campaign_ids: Campaigns to change.
        operation_status: ENABLE (resume) or DISABLE (pause).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.update_campaign_status(campaign_ids, operation_status))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_update_adgroup(adgroup_id: str, fields: dict, advertiser_id: str | None = None) -> dict:
    """Update an ad group (name, bid, schedule, targeting, etc.).

    Args:
        adgroup_id: Ad group to update.
        fields: TikTok /adgroup/update/ fields, e.g. {"adgroup_name": "...", "bid_price": 1.5}.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.update_adgroup(adgroup_id, fields))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_update_adgroup_status(
    adgroup_ids: list[str], operation_status: str, advertiser_id: str | None = None
) -> dict:
    """Pause or resume ad groups. Deleting is not supported.

    Args:
        adgroup_ids: Ad groups to change.
        operation_status: ENABLE (resume) or DISABLE (pause).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.update_adgroup_status(adgroup_ids, operation_status))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_update_adgroup_budget(budgets: list[dict], advertiser_id: str | None = None) -> dict:
    """Change ad group budgets.

    Args:
        budgets: List of {"adgroup_id": "...", "budget": 50.0} entries.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.update_adgroup_budget(budgets))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_create_ad(adgroup_id: str, creatives: list[dict], advertiser_id: str | None = None) -> dict:
    """Create one or more ads in an ad group.

    Args:
        adgroup_id: Parent ad group ID.
        creatives: TikTok /ad/create/ creative objects (ad_name, identity_id, video_id,
            ad_text, call_to_action, landing_page_url, ...).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.create_ad(adgroup_id, creatives))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_update_ad(
    creatives: list[dict], adgroup_id: str | None = None, advertiser_id: str | None = None
) -> dict:
    """Update existing ads (copy, creative, landing page, etc.).

    Args:
        creatives: TikTok /ad/update/ creative objects; each must include ad_id.
        adgroup_id: Ad group ID, if required by the update.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.update_ad(creatives, adgroup_id))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_update_ad_status(
    ad_ids: list[str], operation_status: str, advertiser_id: str | None = None
) -> dict:
    """Pause or resume ads. Deleting is not supported.

    Args:
        ad_ids: Ads to change.
        operation_status: ENABLE (resume) or DISABLE (pause).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.update_ad_status(ad_ids, operation_status))


@mcp.tool(annotations=WRITE)
async def tiktok_ads_create_custom_audience(fields: dict, advertiser_id: str | None = None) -> dict:
    """Create a custom audience.

    Args:
        fields: TikTok /dmp/custom_audience/create/ fields (custom_audience_name,
            file_paths, calculate_type, ...).
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    return await _manage(advertiser_id, lambda m: m.create_custom_audience(fields))


# ============================================================================
# GENERATED TOOLS — every other endpoint in TikTok's official MCP (Smart+, GMV Max,
# catalog, Business Center, pixels, creative, audiences, ...). See api_catalog.py.
# Set TIKTOK_DISABLE_CATALOG=1 to skip, or TIKTOK_TOOL_PREFIXES=smart_plus,gmv_max
# to register only some groups.
# ============================================================================
if os.environ.get("TIKTOK_DISABLE_CATALOG", "").lower() not in ("1", "true", "yes"):
    from .api_catalog import register_catalog

    register_catalog(
        mcp,
        access_token=ACCESS_TOKEN,
        resolve_advertiser=_resolve_advertiser,
        missing_token=_missing_token,
    )

# NOTE: intentionally NOT exposed as tools —
#   creative_tools.create_ad_creative / analyze_creative_performance
#   audience_tools.create_custom_audience / analyze_audience_insights
#     -> return hardcoded MOCK data (would present fabricated metrics as real).
#   creative_tools.upload_image
#     -> reads a file from the SERVER's local disk (image_path); nonfunctional on Railway.
# Re-enable only after they are backed by real TikTok API calls / remote-safe uploads.


def main() -> None:
    """Entry point. Delegates HTTP/stdio bootstrap to __main__.run_server()."""
    from tiktok_ads_mcp.__main__ import run_server
    run_server()


if __name__ == "__main__":
    main()
