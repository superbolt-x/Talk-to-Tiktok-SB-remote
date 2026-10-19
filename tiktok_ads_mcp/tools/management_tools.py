"""Thin wrappers over TikTok Business API endpoints for editing, status changes,
ad-level data, review info, changelog, advertiser info and audiences.

Each method returns {"success": bool, "data": ...} with the raw TikTok payload,
so callers see every field the API returns.
"""

import json
from typing import Any, Dict, List, Optional

from ..tiktok_client import TikTokAdsClient

# Delete is deliberately not exposed — status changes are ENABLE/DISABLE only.
SAFE_OPERATION_STATUSES = {"ENABLE", "DISABLE"}


class ManagementTools:
    def __init__(self, client: TikTokAdsClient):
        self.client = client

    async def _request(self, method: str, endpoint: str, *, params=None, body=None) -> Dict[str, Any]:
        try:
            if method == "POST":
                body = {"advertiser_id": self.client.advertiser_id, **(body or {})}
            result = await self.client._make_request(method, endpoint, params=params, data=body)
            return {"success": True, "data": result.get("data", {})}
        except Exception as e:
            return {"success": False, "data": {"error": str(e), "message": f"Request to {endpoint} failed"}}

    @staticmethod
    def _check_status(operation_status: str) -> Optional[Dict[str, Any]]:
        if operation_status not in SAFE_OPERATION_STATUSES:
            return {
                "success": False,
                "data": {"error": "operation_status must be ENABLE or DISABLE (delete is not supported)."},
            }
        return None

    # ── Reads ────────────────────────────────────────────────────────────────
    async def get_ads(self, adgroup_id=None, campaign_id=None, ad_ids=None, status=None, limit=10, page=1):
        filtering: Dict[str, Any] = {}
        if adgroup_id:
            filtering["adgroup_ids"] = [adgroup_id]
        if campaign_id:
            filtering["campaign_ids"] = [campaign_id]
        if ad_ids:
            filtering["ad_ids"] = ad_ids
        if status:
            filtering["primary_status"] = status
        params: Dict[str, Any] = {"page": page, "page_size": limit}
        if filtering:
            params["filtering"] = filtering
        return await self._request("GET", "ad/get/", params=params)

    async def get_ad_performance(self, level: str, entity_ids: List[str], metrics: List[str],
                                 start_date: str, end_date: str, dimensions: List[str]):
        try:
            result = await self.client.get_performance_data(
                level=level, entity_ids=entity_ids, metrics=metrics,
                start_date=start_date, end_date=end_date, dimensions=dimensions,
            )
            return {"success": True, "data": result.get("data", {})}
        except Exception as e:
            return {"success": False, "data": {"error": str(e), "message": "Failed to retrieve performance"}}

    async def get_ad_review_info(self, ad_ids: List[str]):
        res = await self._request("GET", "ad/review_info/", params={"ad_ids": ad_ids})
        if not res["success"] and "smart plus" in str(res["data"].get("error", "")).lower():
            # Upgraded Smart+ ads aren't supported by this endpoint; report the
            # status fields from ad/get instead.
            ads = await self.get_ads(ad_ids=ad_ids, limit=len(ad_ids) or 10)
            if not ads["success"]:
                return ads
            return {"success": True, "data": {
                "note": "Upgraded Smart+ ads: review endpoint unsupported; showing status fields from ad/get.",
                "ads": [
                    {k: a.get(k) for k in ("ad_id", "ad_name", "primary_status", "secondary_status", "operation_status")}
                    for a in ads["data"].get("list", [])
                ],
            }}
        return res

    async def get_adgroup_review_info(self, adgroup_ids: List[str]):
        return await self._request("GET", "adgroup/review_info/", params={"adgroup_ids": adgroup_ids})

    async def _bc_id(self, bc_id: Optional[str]) -> Optional[str]:
        """Explicit bc_id, else the advertiser's owner_bc_id."""
        if bc_id:
            return bc_id
        info = await self.get_advertiser_info(fields=["owner_bc_id"])
        rows = info["data"].get("list", []) if info["success"] else []
        return rows[0].get("owner_bc_id") if rows else None

    async def get_changelog(self, module: str, start_date: Optional[str] = None,
                            end_date: Optional[str] = None, filtering: Optional[dict] = None,
                            limit: int = 20, page: int = 1, bc_id: Optional[str] = None):
        params: Dict[str, Any] = {"module": module, "page": page, "page_size": limit}
        if bc := await self._bc_id(bc_id):
            params["bc_id"] = bc
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if filtering:
            params["filtering"] = filtering
        return await self._request("GET", "changelog/get/", params=params)

    async def get_advertiser_info(self, fields: Optional[List[str]] = None):
        params: Dict[str, Any] = {"advertiser_ids": [self.client.advertiser_id]}
        if fields:
            params["fields"] = fields
        return await self._request("GET", "advertiser/info/", params=params)

    async def get_advertiser_balance(self, bc_id: Optional[str] = None):
        params: Dict[str, Any] = {}
        if bc := await self._bc_id(bc_id):
            params["bc_id"] = bc
        return await self._request("GET", "advertiser/balance/get/", params=params)

    async def get_saved_audiences(self, limit: int = 10, page: int = 1):
        return await self._request("GET", "dmp/saved_audience/list/", params={"page": page, "page_size": limit})

    async def estimate_audience_size(self, targeting: Dict[str, Any]):
        return await self._request("POST", "ad/audience_size/estimate/", body=targeting)

    # ── Updates / status changes ─────────────────────────────────────────────
    async def update_campaign(self, campaign_id: str, fields: Dict[str, Any]):
        return await self._request("POST", "campaign/update/", body={"campaign_id": campaign_id, **fields})

    async def update_campaign_status(self, campaign_ids: List[str], operation_status: str):
        if err := self._check_status(operation_status):
            return err
        return await self._request("POST", "campaign/status/update/",
                                   body={"campaign_ids": campaign_ids, "operation_status": operation_status})

    async def update_adgroup(self, adgroup_id: str, fields: Dict[str, Any]):
        return await self._request("POST", "adgroup/update/", body={"adgroup_id": adgroup_id, **fields})

    async def update_adgroup_status(self, adgroup_ids: List[str], operation_status: str):
        if err := self._check_status(operation_status):
            return err
        return await self._request("POST", "adgroup/status/update/",
                                   body={"adgroup_ids": adgroup_ids, "operation_status": operation_status})

    async def update_adgroup_budget(self, budgets: List[Dict[str, Any]]):
        return await self._request("POST", "adgroup/budget/update/", body={"budget": budgets})

    async def create_ad(self, adgroup_id: str, creatives: List[Dict[str, Any]]):
        return await self._request("POST", "ad/create/", body={"adgroup_id": adgroup_id, "creatives": creatives})

    async def update_ad(self, creatives: List[Dict[str, Any]], adgroup_id: Optional[str] = None):
        body: Dict[str, Any] = {"creatives": creatives}
        if adgroup_id:
            body["adgroup_id"] = adgroup_id
        return await self._request("POST", "ad/update/", body=body)

    async def update_ad_status(self, ad_ids: List[str], operation_status: str):
        if err := self._check_status(operation_status):
            return err
        return await self._request("POST", "ad/status/update/",
                                   body={"ad_ids": ad_ids, "operation_status": operation_status})

    async def create_custom_audience(self, fields: Dict[str, Any]):
        return await self._request("POST", "dmp/custom_audience/create/", body=fields)
