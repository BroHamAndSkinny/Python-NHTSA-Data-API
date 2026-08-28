#!/usr/bin/env python3
"""
NHTSA Recall Lookup - Standalone recall campaign lookup service
Free, comprehensive recall data from National Highway Traffic Safety Administration

Author: Wal33D
Email: aquataze@yahoo.com
"""

import urllib.request
import urllib.error
import urllib.parse
import json
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from functools import lru_cache


@dataclass
class RecallRecord:
    """Single NHTSA recall campaign entry"""

    manufacturer: Optional[str] = None
    nhtsa_campaign_number: Optional[str] = None
    nhtsa_action_number: Optional[str] = None
    report_received_date: Optional[str] = None
    component: Optional[str] = None
    summary: Optional[str] = None
    consequence: Optional[str] = None
    remedy: Optional[str] = None
    notes: Optional[str] = None
    model_year: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    mfr_recall_number: Optional[str] = None
    over_the_air_update: Optional[bool] = None
    park_it: Optional[bool] = None
    park_outside: Optional[bool] = None
    additional_fields: Dict[str, Any] = None

    def is_critical_safety(self) -> bool:
        """Check if this recall requires immediate attention"""
        return self.park_it == True or self.park_outside == True

    def is_over_the_air(self) -> bool:
        """Check if this recall can be resolved via OTA update"""
        return self.over_the_air_update == True


class NHTSARecallLookup:
    """
    NHTSA Recall Lookup using official Recall API

    Features:
    - Free government API (no key required)
    - Comprehensive recall information
    - Built-in caching
    - Multiple lookup methods
    """

    BASE_URL = "https://api.nhtsa.gov/recalls"

    def __init__(self):
        """Initialize recall lookup service"""
        # Using @lru_cache on methods for caching
        pass

    @lru_cache(maxsize=128)
    def get_recalls_for_vehicle(self, make: str, model: str,
                                model_year: Optional[str] = None) -> List[RecallRecord]:
        """
        Fetch recall campaigns for a vehicle.

        Args:
            make: Vehicle make (e.g., "Honda")
            model: Vehicle model (e.g., "Accord")
            model_year: Optional model year (string or int)

        Returns:
            List of RecallRecord instances.
        """
        normalized_make = self._clean_value(make)
        normalized_model = self._clean_value(model)
        if not normalized_make or not normalized_model:
            return []

        params = {
            "make": normalized_make,
            "model": normalized_model,
        }
        if model_year:
            params["modelYear"] = str(model_year)

        query_string = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}/recallsByVehicle?{query_string}"

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, Exception) as e:
            print(f"Error fetching recalls: {e}")
            return []

        results = data.get("results") or data.get("Results") or []
        recalls: List[RecallRecord] = []

        for entry in results:
            recalls.append(
                RecallRecord(
                    manufacturer=self._clean_value(entry.get("Manufacturer")),
                    nhtsa_campaign_number=self._clean_value(entry.get("NHTSACampaignNumber")
                                                            or entry.get("nhtsaCampaignNumber")),
                    nhtsa_action_number=self._clean_value(entry.get("NHTSAActionNumber")),
                    report_received_date=self._clean_value(entry.get("ReportReceivedDate")
                                                          or entry.get("reportReceivedDate")),
                    component=self._clean_value(entry.get("Component")),
                    summary=self._clean_value(entry.get("Summary")),
                    consequence=self._clean_value(entry.get("Consequence")),
                    remedy=self._clean_value(entry.get("Remedy")),
                    notes=self._clean_value(entry.get("Notes")),
                    model_year=self._clean_value(entry.get("ModelYear") or entry.get("modelYear")),
                    make=self._clean_value(entry.get("Make") or entry.get("make")),
                    model=self._clean_value(entry.get("Model") or entry.get("model")),
                    mfr_recall_number=self._clean_value(entry.get("mfrRecallNumber")
                                                        or entry.get("MfrRecallNumber")),
                    over_the_air_update=self._parse_bool(entry.get("overTheAirUpdate")
                                                          or entry.get("overTheAirUpdateYn")),
                    park_it=self._parse_bool(entry.get("parkIt")),
                    park_outside=self._parse_bool(entry.get("parkOutSide")
                                                  or entry.get("parkOutside")
                                                  or entry.get("parkOutsideYn")),
                    additional_fields=entry,
                )
            )

        return recalls

    @lru_cache(maxsize=128)
    def get_recall_by_campaign_number(self, campaign_number: str) -> Optional[RecallRecord]:
        """
        Get a specific recall by campaign number.

        Args:
            campaign_number: NHTSA campaign number

        Returns:
            RecallRecord or None if not found
        """
        if not campaign_number:
            return None

        params = {"campaignNumber": campaign_number}
        query_string = urllib.parse.urlencode(params)
        url = f"{self.BASE_URL}/campaignNumber?{query_string}"

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, Exception) as e:
            print(f"Error fetching recall: {e}")
            return None

        results = data.get("results") or data.get("Results") or []

        if results and len(results) > 0:
            entry = results[0]
            return RecallRecord(
                manufacturer=self._clean_value(entry.get("Manufacturer")),
                nhtsa_campaign_number=self._clean_value(entry.get("NHTSACampaignNumber")
                                                        or entry.get("nhtsaCampaignNumber")),
                nhtsa_action_number=self._clean_value(entry.get("NHTSAActionNumber")),
                report_received_date=self._clean_value(entry.get("ReportReceivedDate")
                                                      or entry.get("reportReceivedDate")),
                component=self._clean_value(entry.get("Component")),
                summary=self._clean_value(entry.get("Summary")),
                consequence=self._clean_value(entry.get("Consequence")),
                remedy=self._clean_value(entry.get("Remedy")),
                notes=self._clean_value(entry.get("Notes")),
                model_year=self._clean_value(entry.get("ModelYear") or entry.get("modelYear")),
                make=self._clean_value(entry.get("Make") or entry.get("make")),
                model=self._clean_value(entry.get("Model") or entry.get("model")),
                mfr_recall_number=self._clean_value(entry.get("mfrRecallNumber")
                                                    or entry.get("MfrRecallNumber")),
                over_the_air_update=self._parse_bool(entry.get("overTheAirUpdate")
                                                      or entry.get("overTheAirUpdateYn")),
                park_it=self._parse_bool(entry.get("parkIt")),
                park_outside=self._parse_bool(entry.get("parkOutSide")
                                              or entry.get("parkOutside")
                                              or entry.get("parkOutsideYn")),
                additional_fields=entry,
            )

        return None

    def filter_critical_recalls(self, recalls: List[RecallRecord]) -> List[RecallRecord]:
        """
        Filter recalls to only show critical safety recalls.

        Args:
            recalls: List of RecallRecord instances

        Returns:
            List of critical recalls only
        """
        return [recall for recall in recalls if recall.is_critical_safety()]

    def filter_by_component(self, recalls: List[RecallRecord], component_keyword: str) -> List[RecallRecord]:
        """
        Filter recalls by component keyword.

        Args:
            recalls: List of RecallRecord instances
            component_keyword: Keyword to search in component field

        Returns:
            Filtered list of recalls
        """
        if not component_keyword:
            return recalls

        keyword_lower = component_keyword.lower()
        return [recall for recall in recalls
                if recall.component and keyword_lower in recall.component.lower()]

    def group_by_year(self, recalls: List[RecallRecord]) -> Dict[str, List[RecallRecord]]:
        """
        Group recalls by model year.

        Args:
            recalls: List of RecallRecord instances

        Returns:
            Dictionary with years as keys and recall lists as values
        """
        grouped = {}
        for recall in recalls:
            year = recall.model_year or "Unknown"
            if year not in grouped:
                grouped[year] = []
            grouped[year].append(recall)
        return grouped

    def clear_cache(self):
        """Clear cached recall lookups"""
        self.get_recalls_for_vehicle.cache_clear()
        self.get_recall_by_campaign_number.cache_clear()

    @staticmethod
    def _clean_value(value: any) -> Optional[str]:
        """Clean API response values"""
        if value is None:
            return None
        value = str(value).strip()
        if value.lower() in ['', 'null', 'none', 'not applicable']:
            return None
        return value

    @staticmethod
    def _parse_bool(value: Any) -> Optional[bool]:
        """Parse boolean values from API response"""
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            if value == 1:
                return True
            if value == 0:
                return False
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"y", "yes", "true", "t", "1"}:
                return True
            if lowered in {"n", "no", "false", "f", "0"}:
                return False
        return None


def main():
    """Example usage"""
    lookup = NHTSARecallLookup()

    # Test recall lookup by make/model/year
    print("Testing recall lookup for 2019 Honda CR-V...")
    recalls = lookup.get_recalls_for_vehicle("Honda", "CR-V", "2019")

    print(f"\nFound {len(recalls)} recalls")

    for i, recall in enumerate(recalls[:3], 1):
        print(f"\nRecall #{i}:")
        print(f"  Campaign: {recall.nhtsa_campaign_number}")
        print(f"  Component: {recall.component}")
        print(f"  Summary: {recall.summary[:100] + '...' if recall.summary and len(recall.summary) > 100 else recall.summary}")

        if recall.is_critical_safety():
            print("  ⚠️  CRITICAL SAFETY RECALL")
        if recall.is_over_the_air():
            print("  ✓ Can be fixed via over-the-air update")

    # Test filtering
    critical_recalls = lookup.filter_critical_recalls(recalls)
    if critical_recalls:
        print(f"\n⚠️  {len(critical_recalls)} critical safety recalls found!")

    # Test grouping by year
    print("\n\nTesting all years for Toyota Camry...")
    all_recalls = lookup.get_recalls_for_vehicle("Toyota", "Camry")
    grouped = lookup.group_by_year(all_recalls)

    print(f"Recalls by year:")
    for year in sorted(grouped.keys(), reverse=True)[:5]:
        print(f"  {year}: {len(grouped[year])} recalls")


if __name__ == "__main__":
    main()