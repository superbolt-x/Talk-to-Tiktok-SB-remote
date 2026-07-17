"""Performance analytics tools for TikTok Ads MCP server."""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..tiktok_client import TikTokAdsClient
from .reporting_tools import GMV_METRICS


class PerformanceTools:
    """Tools for retrieving TikTok Ads performance data and analytics."""
    
    def __init__(self, client: TikTokAdsClient):
        self.client = client
    
    def _get_date_range(self, date_range: str) -> tuple[str, str]:
        """Convert date range string to start and end dates.
        
        Args:
            date_range: Date range string (today, yesterday, last_7_days, etc.)
            
        Returns:
            Tuple of (start_date, end_date) in YYYY-MM-DD format
        """
        today = datetime.now()
        
        if date_range == "today":
            start_date = end_date = today.strftime("%Y-%m-%d")
        elif date_range == "yesterday":
            yesterday = today - timedelta(days=1)
            start_date = end_date = yesterday.strftime("%Y-%m-%d")
        elif date_range == "last_7_days":
            start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        elif date_range == "last_14_days":
            start_date = (today - timedelta(days=14)).strftime("%Y-%m-%d")
            end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        elif date_range == "last_30_days":
            start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
            end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            # Default to last 7 days
            start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            end_date = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        
        return start_date, end_date
    

    async def get_campaign_performance(
        self,
        campaign_ids: List[str],
        date_range: str = "last_7_days",
        metrics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get performance metrics for campaigns.
        
        Args:
            campaign_ids: List of campaign IDs to analyze
            date_range: Date range for performance data
            metrics: Specific metrics to include (optional)
            
        Returns:
            Campaign performance data
        """
        try:
            start_date, end_date = self._get_date_range(date_range)
            dimensions = [
                "campaign_id"
            ]
            
            # Default metrics if not specified
            common_metrics = ["campaign_name"]
            if not metrics:
                metrics = [
                    "impressions", "clicks", "conversion", "spend",
                    "ctr", "cpm", "cpc", "conversion_rate_v2"
                ]
            else:
                rewrite_metrics = {
                    "conversions": "conversion"
                }
                metrics = [rewrite_metrics.get(m, m) for m in metrics]
            
            result = await self.client.get_performance_data(
                level="AUCTION_CAMPAIGN",
                entity_ids=campaign_ids,
                metrics=common_metrics+metrics,
                dimensions=dimensions,
                start_date=start_date,
                end_date=end_date,
            )
            
            performance_data = [item.get("metrics", {}) for item in  result.get("data", {}).get("list", [])]
            total_metrics = result.get("data", {}).get("total_metrics", {})
            
            
            return {
                "success": True,
                "date_range": f"{start_date} to {end_date}",
                "campaigns": performance_data,
                "totals": total_metrics,
                "campaign_count": len(performance_data),
                "message": f"Retrieved performance data for {len(performance_data)} campaigns"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "campaign_ids": campaign_ids,
                "date_range": date_range,
                "message": "Failed to retrieve campaign performance data"
            }
    
    async def get_adgroup_performance(
        self,
        adgroup_ids: List[str],
        date_range: str = "last_7_days",
        metrics: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Get performance metrics for ad groups.
        
        Args:
            adgroup_ids: List of ad group IDs to analyze
            date_range: Date range for performance data
            breakdowns: Data breakdowns (age, gender, country, placement)
            
        Returns:
            Ad group performance data
        """
        try:
            start_date, end_date = self._get_date_range(date_range)
            dimensions = [
                "adgroup_id"
            ]
            # Standard metrics for ad group analysis
            common_metrics = ["campaign_id", "campaign_name", "adgroup_name"]
            if not metrics:
                metrics = [
                    "impressions", "clicks", "conversion", "spend",
                    "ctr", "cpm", "cpc", "conversion_rate_v2"
                ]
            else:
                rewrite_metrics = {
                    "conversions": "conversion"
                }
                metrics = [rewrite_metrics.get(m, m) for m in metrics]
            result = await self.client.get_performance_data(
                level="AUCTION_ADGROUP",
                entity_ids=adgroup_ids,
                metrics=common_metrics+metrics,
                start_date=start_date,
                end_date=end_date,
                dimensions=dimensions,
            )
            
            performance_data = [item.get("metrics", {}) for item in  result.get("data", {}).get("list", [])]
            total_metrics = result.get("data", {}).get("total_metrics", {})
            
            
            return {
                "success": True,
                "date_range": f"{start_date} to {end_date}",
                "adgroups": performance_data,
                "totals": total_metrics,
                "adgroup_count": len(performance_data),
                "message": f"Retrieved performance data for {len(performance_data)} ad groups"
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "adgroup_ids": adgroup_ids,
                "date_range": date_range,
                "message": "Failed to retrieve ad group performance data"
            }

    async def get_gmv_performance(
        self,
        entity_ids: List[str],
        level: str = "campaign",
        date_range: str = "last_7_days",
    ) -> Dict[str, Any]:
        """Get payment/GMV performance metrics joined with budget context.

        Standard website/shopping conversion campaigns only — not GMV Max.

        Args:
            entity_ids: Campaign IDs (level='campaign') or ad group IDs (level='adgroup').
            level: 'campaign' or 'adgroup'.
            date_range: today, yesterday, last_7_days, last_14_days, last_30_days.

        Returns:
            Per-entity payment/GMV metrics + budget/budget_mode(/spent_budget for campaigns).
        """
        if level not in ("campaign", "adgroup"):
            return {
                "success": False,
                "error": "Invalid level",
                "message": "level must be 'campaign' or 'adgroup'",
            }

        try:
            start_date, end_date = self._get_date_range(date_range)

            if level == "campaign":
                dimensions = ["campaign_id"]
                common_metrics = ["campaign_name"]
                api_level = "AUCTION_CAMPAIGN"
                id_field = "campaign_id"
            else:
                dimensions = ["adgroup_id"]
                common_metrics = ["campaign_id", "campaign_name", "adgroup_name"]
                api_level = "AUCTION_ADGROUP"
                id_field = "adgroup_id"

            result = await self.client.get_performance_data(
                level=api_level,
                entity_ids=entity_ids,
                metrics=common_metrics + GMV_METRICS,
                dimensions=dimensions,
                start_date=start_date,
                end_date=end_date,
            )

            performance_data = [item.get("metrics", {}) for item in result.get("data", {}).get("list", [])]
            total_metrics = result.get("data", {}).get("total_metrics", {})

            # Join budget context, same fields campaign_tools.py surfaces for campaigns.
            budgets_by_id: Dict[str, Dict[str, Any]] = {}
            if level == "campaign":
                budget_result = await self.client.get_campaigns_by_ids(entity_ids)
                for c in budget_result.get("data", {}).get("list", []):
                    budgets_by_id[c.get("campaign_id")] = {
                        "budget": c.get("budget"),
                        "budget_mode": c.get("budget_mode"),
                        "spent_budget": c.get("spent_budget", 0),
                    }
            else:
                budget_result = await self.client.get_adgroups_by_ids(entity_ids)
                for a in budget_result.get("data", {}).get("list", []):
                    budgets_by_id[a.get("adgroup_id")] = {
                        "budget": a.get("budget"),
                        "bid_type": a.get("bid_type"),
                    }

            for row in performance_data:
                row.update(budgets_by_id.get(row.get(id_field), {}))

            return {
                "success": True,
                "level": level,
                "date_range": f"{start_date} to {end_date}",
                "data": performance_data,
                "totals": total_metrics,
                "entity_count": len(performance_data),
                "message": f"Retrieved GMV/payment performance for {len(performance_data)} {level}(s)"
            }

        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "entity_ids": entity_ids,
                "level": level,
                "date_range": date_range,
                "message": "Failed to retrieve GMV performance data"
            }