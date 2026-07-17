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

# Allow the Railway hostname through the MCP SDK's DNS-rebinding protection,
# otherwise every POST /mcp returns 421 "Invalid Host header".
if SERVER_URL:
    _host = urlparse(SERVER_URL).netloc
    _transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[_host, f"{_host}:*"],
        allowed_origins=[SERVER_URL, f"{SERVER_URL}:*"],
    )
else:
    # Local/dev — no public host to pin.
    _transport_security = TransportSecuritySettings(enable_dns_rebinding_protection=False)

mcp = FastMCP(
    "Talk-to-TikTok",
    instructions=(
        "Talk-to-TikTok connects TikTok Ads (TikTok Business API) to Claude. "
        "Read tools retrieve campaigns, ad groups, performance, creatives, audiences, "
        "and reports. Write tools create campaigns and ad groups. "
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
async def tiktok_ads_get_gmv_performance(
    entity_ids: list[str],
    level: str = "campaign",
    date_range: str = "last_7_days",
    advertiser_id: str | None = None,
) -> dict:
    """Get payment value ("GMV") / shopping conversions for campaigns or ad groups,
    joined with their budget context. Standard website/shopping conversion campaigns
    only (we run no GMV Max campaigns) — metrics: complete_payment, total_complete_payment,
    complete_payment_roas, value_per_complete_payment, cost_per_complete_payment,
    onsite_shopping, total_onsite_shopping_value, total_purchase_value.

    Args:
        entity_ids: Campaign IDs (level='campaign') or ad group IDs (level='adgroup').
        level: 'campaign' or 'adgroup'.
        date_range: One of today, yesterday, last_7_days, last_14_days, last_30_days.
        advertiser_id: Advertiser ID (defaults to the selected/default account).
    """
    if not ACCESS_TOKEN:
        return _missing_token()
    adv = _resolve_advertiser(advertiser_id)
    if not adv:
        return _missing_advertiser()
    client = _client(adv)
    try:
        return await PerformanceTools(client).get_gmv_performance(
            entity_ids=entity_ids, level=level, date_range=date_range
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
    filtering: dict | None = None,
    advertiser_id: str | None = None,
) -> dict:
    """Start an asynchronous custom performance report. Returns a task_id to poll
    with tiktok_ads_get_report_status and fetch with tiktok_ads_download_report.

    Args:
        report_type: BASIC, AUDIENCE, PLACEMENT, or DPA.
        dimensions: e.g. campaign_id, adgroup_id, ad_id, stat_time_day.
        metrics: e.g. impressions, clicks, spend, ctr, cpm, cpc, conversion.
        start_date: YYYY-MM-DD.
        end_date: YYYY-MM-DD.
        filtering: Optional TikTok report filtering object.
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
