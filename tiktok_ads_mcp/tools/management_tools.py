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
        res = await self._request("GET", "ad/get/", params=params)
        if res["success"]:
            await self._add_smart_plus_status(res["data"])
        return res

    @staticmethod
    def _is_smart_plus(row: Dict[str, Any]) -> bool:
        return "SMART_PLUS" in json.dumps(row, default=str).upper()

    @staticmethod
    def _is_disabled(row: Dict[str, Any]) -> bool:
        return any("DISABLE" in str(row.get(k, "")).upper()
                   for k in ("operation_status", "secondary_status", "primary_status"))

    @staticmethod
    def _all_strings(obj: Any, out: set) -> set:
        """Every string/int leaf inside a nested JSON value."""
        if isinstance(obj, dict):
            for v in obj.values():
                ManagementTools._all_strings(v, out)
        elif isinstance(obj, list):
            for v in obj:
                ManagementTools._all_strings(v, out)
        elif isinstance(obj, (str, int)) and not isinstance(obj, bool):
            out.add(str(obj))
        return out

    @staticmethod
    def _row_ids(row: Dict[str, Any]) -> set:
        """Identifiers on an ad/get row that could also appear inside a Smart+ row."""
        skip = {"advertiser_id", "campaign_id", "adgroup_id"}
        return {str(v) for k, v in row.items()
                if k.endswith("_id") and k not in skip and isinstance(v, (str, int)) and v}

    def _apply_smart_plus_status(self, rows: List[Dict[str, Any]], sp_rows: List[Dict[str, Any]]) -> None:
        """Write the Smart+ status into the matching ad/get rows.

        A row is matched to a Smart+ ad when one of its ids (ad_id, creative ids, ...) appears
        anywhere inside the Smart+ row. Originals are kept as ad_get_*; rows that can't be
        matched are marked so nobody trusts their ad/get status.
        """
        index = [(sp, self._all_strings(sp, set())) for sp in sp_rows]
        smart_rows = [r for r in rows if self._is_smart_plus(r)]
        # Ids shared by several ads (identity, pixel, ...) would mis-match; use only ids unique to one row.
        counts: Dict[str, int] = {}
        for r in smart_rows:
            for i in self._row_ids(r):
                counts[i] = counts.get(i, 0) + 1
        for row in smart_rows:
            ids = {i for i in self._row_ids(row) if counts[i] == 1}
            matches = [sp for sp, vals in index if ids & vals]
            match = matches[0] if len(matches) == 1 else None  # ambiguous => unmatched
            if match is None:
                row["status_source"] = "ad/get (could not match to a Smart+ ad; status may be wrong)"
                continue
            for k in ("operation_status", "secondary_status"):
                if k in match:
                    row[f"ad_get_{k}"] = row.get(k)
                    row[k] = match[k]
            row["smart_plus_ad_id"] = match.get("smart_plus_ad_id")
            row["status_source"] = "smart_plus/ad/get"

    async def _add_smart_plus_status(self, data: Dict[str, Any]) -> None:
        """ad/get reports ENABLE/DELIVERY_OK for Smart+ ads that are disabled at the
        Smart+ level. When the result contains Smart+ ads, also fetch smart_plus/ad/get
        for the same ad groups and attach it, with the disabled ones called out.
        """
        rows = data.get("list") or []
        smart = [r for r in rows if self._is_smart_plus(r)]
        if not smart:
            return
        data["smart_plus_warning"] = (
            "Some ads are Upgraded Smart+ ads. ad/get can report them as ENABLE/DELIVERY_OK even when "
            "they are disabled. Rows matched to a Smart+ ad have operation_status/secondary_status taken from "
            "smart_plus/ad/get (originals kept as ad_get_*; see status_source). Unmatched rows are marked; trust "
            "smart_plus_ads for them. Note ad_id may be a creative ID, while smart_plus_ads uses smart_plus_ad_id."
        )
        adgroup_ids = sorted({str(r["adgroup_id"]) for r in smart if r.get("adgroup_id")})
        if not adgroup_ids:
            return
        sp = await self._request("GET", "smart_plus/ad/get/", params={
            "filtering": {"adgroup_ids": adgroup_ids}, "page_size": 100,
        })
        if not sp["success"]:
            data["smart_plus_error"] = sp["data"].get("error")
            return
        sp_rows = sp["data"].get("list") or []
        data["smart_plus_ads"] = sp_rows
        self._apply_smart_plus_status(rows, sp_rows)
        data["smart_plus_disabled"] = [
            {k: r.get(k) for k in ("smart_plus_ad_id", "ad_name", "operation_status", "secondary_status")}
            for r in sp_rows if self._is_disabled(r)
        ]

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
            d = ads["data"]
            out = {
                "note": "Upgraded Smart+ ads: review endpoint unsupported; showing status fields from ad/get, "
                        "plus smart_plus/ad/get where available (the source of truth for Smart+ status).",
                "ads": [
                    {k: a.get(k) for k in ("ad_id", "ad_name", "primary_status", "secondary_status", "operation_status")}
                    for a in d.get("list", [])
                ],
            }
            for k in ("smart_plus_warning", "smart_plus_ads", "smart_plus_disabled", "smart_plus_error"):
                if k in d:
                    out[k] = d[k]
            return {"success": True, "data": out}
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
