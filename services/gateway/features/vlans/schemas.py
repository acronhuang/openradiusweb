"""Public data surface for the vlans feature.

Re-exports VLAN Pydantic models from `orw_common.models.vlan`
because they are shared with policy_engine and freeradius_config.
"""
from orw_common.models.vlan import VlanCreate, VlanUpdate

__all__ = ["VlanCreate", "VlanUpdate"]
