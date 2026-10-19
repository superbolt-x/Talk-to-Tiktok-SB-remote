"""Generated tools for the TikTok Business API endpoints in TikTok's official MCP.

Tools already hand-written in server.py are skipped; the rest are generated from
api_endpoints.ENDPOINTS as `tiktok_ads_<official_name>(args, advertiser_id)`. Each
tool is bound to exactly one documented endpoint — there is no generic "call any
path" tool.

`args` carries TikTok's documented fields: sent as the query string for GET
endpoints and as the JSON body for POST endpoints. The HTTP method is inferred
from the tool name; if TikTok rejects it with 405, the call is retried with the
other method.
"""
import asyncio
import logging
import os
import time
from typing import Any, Callable

from mcp.types import ToolAnnotations

from .api_endpoints import ENDPOINTS
from .tiktok_client import TikTokAdsClient

logger = logging.getLogger("talk-to-tiktok.catalog")

READONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True)

# Covered by hand-written tools in server.py.
HANDWRITTEN = {
    "ad_audience_size_estimate", "ad_create", "ad_get", "ad_review_info_get", "ad_status_update", "ad_update",
    "adgroup_budget_update", "adgroup_create", "adgroup_get", "adgroup_review_info_get", "adgroup_status_update",
    "adgroup_update", "advertiser_balance_get", "advertiser_info_get", "campaign_create", "campaign_get",
    "campaign_status_update", "campaign_update", "changelog_get", "dmp_custom_audience_create",
    "dmp_custom_audience_list_get", "dmp_saved_audience_list_get", "report_integrated_get",
    "report_task_check", "report_task_create", "report_task_download",
}

# Deliberately not exposed: these move money between accounts.
EXCLUDED_MONEY = {"bc_transfer", "payment_portfolio_credit_line_update"}

# Names that say "get"/"run" but which TikTok serves as POST (best knowledge; the 405
# retry covers mistakes), and the reverse.
FORCE_POST = {
    "split_test_end_get", "creative_smart_text_get", "creative_image_edit_get", "tool_search_keyword_recommend",
    "tool_bid_recommend", "rf_inventory_estimate", "audience_segment_operate", "blockedword_check",
    "tool_url_validate", "file_name_check", "split_test_promote_run", "dmp_custom_audience_apply",
}
FORCE_GET = {
    "smart_plus_material_report_breakdown_run", "smart_plus_material_report_overview_run",
    "dmp_custom_audience_apply_log_get", "catalog_eventsource_bind_get", "tool_vbo_status_check",
    "gmv_max_occupied_custom_shop_ads_list_check", "gmv_max_store_shop_ad_usage_check_check",
    "term_check", "bc_advertiser_unionpay_info_check", "campaign_copy_task_check",
    "changelog_task_check", "blockedword_task_check", "comment_task_check", "dmp_custom_audience_share_get",
}
POST_WORDS = (
    "create", "update", "delete", "apply", "assign", "unassign", "invite", "add", "submit", "cancel",
    "transfer", "appeal", "bind", "unbind", "subscribe", "unsubscribe", "confirm", "save", "upload", "operate",
    "authorize", "link", "disable", "validate", "estimate", "migrate", "post", "preview",
)
# POST endpoints that only compute/preview and change nothing.
READONLY_POST = {
    "rf_inventory_estimate", "blockedword_check", "tool_url_validate", "file_name_check", "playable_validate",
    "smart_plus_ad_preview", "creative_ads_preview_create", "creative_smart_text_get", "creative_image_edit_get",
    "tool_bid_recommend", "tool_search_keyword_recommend", "audience_segment_operate",
}
DESTRUCTIVE_WORDS = ("delete", "unassign", "unbind", "cancel", "disable", "unsubscribe", "_end_")
# No advertiser_id by default (account-level / Business Center endpoints).
NO_ADVERTISER_PREFIXES = (
    "bc/", "oauth2/", "user/", "payment_portfolio/", "asset/", "tto/", "tcm/", "subscription/", "term/",
)

# TikTok asks for at most one op per 5s per Upgraded Smart+ ad.
SMART_PLUS_THROTTLED = {"smart_plus_ad_create", "smart_plus_ad_update", "smart_plus_ad_status_update"}
_throttle_lock = asyncio.Lock()
_last_call: dict[str, float] = {}


def parse() -> list[tuple[str, str]]:
    return [tuple(line.split(" ", 1)) for line in ENDPOINTS.strip().splitlines()]  # type: ignore[misc]


def infer_method(name: str) -> str:
    if name in FORCE_POST:
        return "POST"
    if name in FORCE_GET:
        return "GET"
    if name.endswith(("_get", "_list", "_info", "_check", "_run", "_search", "_query")) and "_create" not in name:
        return "GET"
    return "POST" if any(w in name for w in POST_WORDS) else "GET"


def annotation_for(name: str, method: str) -> ToolAnnotations:
    if method == "GET" or name in READONLY_POST:
        return READONLY
    if any(w in name for w in DESTRUCTIVE_WORDS):
        return DESTRUCTIVE
    return WRITE


def _selected(name: str) -> bool:
    """TIKTOK_TOOL_PREFIXES=smart_plus,gmv_max limits which generated tools register."""
    prefixes = [p.strip() for p in os.environ.get("TIKTOK_TOOL_PREFIXES", "").split(",") if p.strip()]
    return not prefixes or any(name.startswith(p) for p in prefixes)


async def _throttle(advertiser: str | None, name: str) -> None:
    async with _throttle_lock:
        key = f"{advertiser}:{name}"
        wait = 5 - (time.monotonic() - _last_call.get(key, 0))
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call[key] = time.monotonic()


async def _call(client: TikTokAdsClient, method: str, path: str, args: dict[str, Any]) -> dict:
    path = path.rstrip("/") + "/"  # TikTok endpoints require the trailing slash
    async def once(m: str) -> dict:
        if m == "GET":
            return await client._make_request("GET", path, params=args)
        body = dict(args)
        if client.advertiser_id and not client.skip_advertiser and "advertiser_id" not in body:
            body["advertiser_id"] = client.advertiser_id
        return await client._make_request("POST", path, data=body)

    try:
        result = await once(method)
    except Exception as e:
        if "405" not in str(e) and "method not allowed" not in str(e).lower():
            return {"success": False, "data": {"error": str(e), "message": f"Request to {path} failed"}}
        other = "POST" if method == "GET" else "GET"
        try:
            result = await once(other)
        except Exception as e2:
            return {"success": False, "data": {
                "error": str(e2), "message": f"Request to {path} failed ({method} and {other})"}}
    return {"success": True, "data": result.get("data", {})}


def register_catalog(
    mcp,
    *,
    access_token: str,
    resolve_advertiser: Callable[[str | None], str | None],
    missing_token: Callable[[], dict],
) -> int:
    """Register all generated tools on `mcp`. Returns how many were added."""
    count = 0

    def make(name: str, path: str, method: str):
        needs_advertiser = not path.startswith(NO_ADVERTISER_PREFIXES)

        async def tool(args: dict | None = None, advertiser_id: str | None = None) -> dict:
            if not access_token:
                return missing_token()
            adv = resolve_advertiser(advertiser_id) if (needs_advertiser or advertiser_id) else None
            if needs_advertiser and not adv:
                return {"success": False, "error": "No advertiser selected.",
                        "message": "Pass advertiser_id, set TIKTOK_ADVERTISER_ID, or call tiktok_ads_switch_ad_account."}
            if name in SMART_PLUS_THROTTLED:
                await _throttle(adv, name)
            client = TikTokAdsClient(access_token=access_token, advertiser_id=adv, skip_advertiser=not adv)
            try:
                return await _call(client, method, path, dict(args or {}))
            finally:
                await client.close()

        tool.__name__ = f"tiktok_ads_{name}"
        return tool

    for name, path in parse():
        if name in HANDWRITTEN or name in EXCLUDED_MONEY or not _selected(name):
            continue
        method = infer_method(name)
        scope = "account-level (no advertiser needed)" if path.startswith(NO_ADVERTISER_PREFIXES) else "advertiser-level"
        mcp.add_tool(
            make(name, path, method),
            name=f"tiktok_ads_{name}",
            description=(
                f"TikTok Business API /v1.3/{path}/ ({method}), {scope}.\n\n"
                "Args:\n"
                f"    args: TikTok's documented fields for this endpoint ({'query params' if method == 'GET' else 'JSON body'}).\n"
                "    advertiser_id: Advertiser ID (defaults to the selected/default account)."
            ),
            annotations=annotation_for(name, method),
        )
        count += 1

    logger.info("Registered %d generated API tools", count)
    return count
