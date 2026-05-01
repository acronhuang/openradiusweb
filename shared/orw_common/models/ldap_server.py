"""LDAP server models."""

from typing import Optional

from pydantic import BaseModel, Field


class LDAPServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(389, ge=1, le=65535)
    use_tls: bool = False
    use_starttls: bool = False
    bind_dn: Optional[str] = Field(None, max_length=512)
    bind_password: Optional[str] = None
    base_dn: str = Field(..., min_length=1, max_length=512)
    user_search_filter: str = Field("(sAMAccountName={0})", max_length=512)
    user_search_base: Optional[str] = Field(None, max_length=512)
    group_search_filter: Optional[str] = Field(None, max_length=512)
    group_search_base: Optional[str] = Field(None, max_length=512)
    group_membership_attr: str = Field("memberOf", max_length=128)
    username_attr: str = Field("sAMAccountName", max_length=128)
    display_name_attr: str = Field("displayName", max_length=128)
    email_attr: str = Field("mail", max_length=128)
    connect_timeout_seconds: int = Field(5, ge=1, le=30)
    search_timeout_seconds: int = Field(10, ge=1, le=60)
    idle_timeout_seconds: int = Field(300, ge=30, le=3600)
    tls_ca_cert: Optional[str] = None
    # tls_require_cert is a VARCHAR enum in the schema (DEFAULT 'demand'),
    # not a bool. Allowed values per OpenLDAP / FreeRADIUS rlm_ldap docs:
    # 'never' / 'allow' / 'try' / 'demand'. Old code typed this as bool
    # which made asyncpg reject the request with "expected str, got bool".
    tls_require_cert: str = Field("demand", pattern="^(never|allow|try|demand)$")
    priority: int = Field(100, ge=0, le=9999)
    enabled: bool = True


class LDAPServerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    host: Optional[str] = Field(None, min_length=1, max_length=255)
    port: Optional[int] = Field(None, ge=1, le=65535)
    use_tls: Optional[bool] = None
    use_starttls: Optional[bool] = None
    bind_dn: Optional[str] = Field(None, max_length=512)
    bind_password: Optional[str] = None
    base_dn: Optional[str] = Field(None, min_length=1, max_length=512)
    user_search_filter: Optional[str] = Field(None, max_length=512)
    user_search_base: Optional[str] = Field(None, max_length=512)
    group_search_filter: Optional[str] = Field(None, max_length=512)
    group_search_base: Optional[str] = Field(None, max_length=512)
    group_membership_attr: Optional[str] = Field(None, max_length=128)
    username_attr: Optional[str] = Field(None, max_length=128)
    display_name_attr: Optional[str] = Field(None, max_length=128)
    email_attr: Optional[str] = Field(None, max_length=128)
    connect_timeout_seconds: Optional[int] = Field(None, ge=1, le=30)
    search_timeout_seconds: Optional[int] = Field(None, ge=1, le=60)
    idle_timeout_seconds: Optional[int] = Field(None, ge=30, le=3600)
    tls_ca_cert: Optional[str] = None
    tls_require_cert: Optional[str] = Field(None, pattern="^(never|allow|try|demand)$")
    priority: Optional[int] = Field(None, ge=0, le=9999)
    enabled: Optional[bool] = None
