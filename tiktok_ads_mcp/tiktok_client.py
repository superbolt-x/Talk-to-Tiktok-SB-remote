"""TikTok Ads API client implementation."""

import json
from typing import Any, Dict, List, Optional
from six import string_types
from six.moves.urllib.parse import urlencode

import httpx


class TikTokAdsClient:
    """TikTok Ads API client for making authenticated requests."""

    BASE_URL = "https://business-api.tiktok.com/open_api/v1.3"

    def __init__(
        self,
        access_token: str,
        advertiser_id: Optional[str] = None,
        available_advertiser_ids: Optional[list] = None,
    ):
        self.access_token = access_token
        self.advertiser_id = advertiser_id
        self.available_advertiser_ids = available_advertiser_ids or []
        self.client = httpx.AsyncClient(timeout=30.0)

    async def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/{endpoint.lstrip('/')}"

        common_params = {
            "advertiser_id": self.advertiser_id,
            "advertiser_ids": [self.advertiser_id],
        }
        headers = {"Access-Token": self.access_token}

        if params:
            common_params.update(params)
        query_string = urlencode({k: v if isinstance(v, string_types) else json.dumps(v) for k, v in common_params.items()})

        try:
            if method.upper() == "GET":
                response = await self.client.get(url + "?" + query_string, headers=headers)
            elif method.upper() == "POST":
                if files:
                    response = await self.client.post(
                        url, params=common_params, files=files, data=data or {}, headers=headers
                    )
                else:
                    response = await self.client.post(
                        url, params=common_params, json=data or {}, headers=headers
                    )
            else:
                raise ValueError(f"Unsupported HTTP method: {method}")

            response.raise_for_status()
            result = response.json()

            if result.get("code") != 0:
                raise Exception(f"TikTok API Error: {result.get('message', 'Unknown error')}")

            return result

        except httpx.HTTPError as e:
            raise Exception(f"HTTP request failed: {e}")
        except json.JSONDecodeError as e:
            raise Exception(f"Failed to parse API response: {e}")

    async def get_campaigns(
        self,
        status: Optional[str] = None,
        limit: int = 10,
        page: int = 1,
    ) -> Dict[str, Any]:
        params = {"page": page, "page_size": limit}
        if status:
            params["filtering"] = json.dumps({"primary_status": status})
        return await self._make_request("GET", "campaign/get/", params=params)

    async def get_campaign_details(self, campaign_id: str) -> Dict[str, Any]:
        params = {"filtering": json.dumps({"campaign_ids": [campaign_id]})}
        return await self._make_request("GET", "campaign/get/", params=params)

    async def create_campaign(self, campaign_data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._make_request("POST", "campaign/create/", data=campaign_data)

    async def get_adgroups(
        self,
        campaign_id: str,
        status: Optional[str] = None,
        limit: int = 10,
        page: int = 1,
    ) -> Dict[str, Any]:
        filtering = {"campaign_ids": [campaign_id]}
        if status:
            filtering["primary_status"] = status
        params = {"filtering": json.dumps(filtering), "page": page, "page_size": limit}
        return await self._make_request("GET", "adgroup/get/", params=params)

    async def create_adgroup(self, adgroup_data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._make_request("POST", "adgroup/create/", data=adgroup_data)

    async def get_performance_data(
        self,
        level: str,
        entity_ids: List[str],
        metrics: List[str],
        start_date: str,
        end_date: str,
        dimensions: List[str],
        breakdowns: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        params = {
            "report_type": "BASIC",
            "data_level": level,
            "start_date": start_date,
            "end_date": end_date,
            "metrics": metrics,
            "dimensions": dimensions,
            "enable_total_metrics": True,
        }

        field_map = {
            "AUCTION_CAMPAIGN": "campaign_ids",
            "AUCTION_ADGROUP": "adgroup_ids",
            "AUCTION_AD": "ad_ids",
        }
        if level in field_map:
            params["filtering"] = [
                {"field_name": field_map[level], "filter_type": "IN", "filter_value": json.dumps(entity_ids)}
            ]

        return await self._make_request("GET", "report/integrated/get/", params=params)

    async def get_ad_creatives(self, limit: int = 10, page: int = 1) -> Dict[str, Any]:
        params = {"page": page, "page_size": limit}
        return await self._make_request("GET", "creative/get/", params=params)

    async def upload_image(self, image_path: str, upload_type: str = "UPLOAD_BY_FILE") -> Dict[str, Any]:
        with open(image_path, "rb") as f:
            return await self._make_request("POST", "file/image/ad/upload/", data={"upload_type": upload_type}, files={"image_file": f})

    async def get_custom_audiences(self, limit: int = 10, page: int = 1) -> Dict[str, Any]:
        params = {"page": page, "page_size": limit}
        return await self._make_request("GET", "dmp/custom_audience/list/", params=params)

    async def get_targeting_options(self, option_type: str, country_code: Optional[str] = None) -> Dict[str, Any]:
        params = {"type": option_type}
        if country_code:
            params["country_code"] = country_code
        return await self._make_request("GET", "tools/target_recommend/", params=params)

    async def create_report_task(
        self,
        report_type: str,
        dimensions: List[str],
        metrics: List[str],
        start_date: str,
        end_date: str,
        filtering: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        data = {
            "report_type": report_type,
            "data_level": "AUCTION_CAMPAIGN",
            "dimensions": dimensions,
            "metrics": metrics,
            "start_date": start_date,
            "end_date": end_date,
        }
        if filtering:
            data["filtering"] = filtering
        return await self._make_request("POST", "report/task/create/", data=data)

    async def get_report_task_status(self, task_id: str) -> Dict[str, Any]:
        return await self._make_request("GET", "report/task/check/", params={"task_id": task_id})

    async def download_report(self, task_id: str) -> Dict[str, Any]:
        return await self._make_request("GET", "report/task/download/", params={"task_id": task_id})

    async def close(self):
        await self.client.aclose()
