"""Domain exception types for OpenRadiusWeb."""


class DomainError(Exception):
    """Base exception for all domain errors."""

    def __init__(self, message: str = "An error occurred"):
        self.message = message
        super().__init__(self.message)


class NotFoundError(DomainError):
    """Resource not found."""

    def __init__(self, resource: str, resource_id: str | None = None):
        self.resource = resource
        self.resource_id = resource_id
        msg = f"{resource} not found"
        if resource_id:
            msg = f"{resource} '{resource_id}' not found"
        super().__init__(msg)


class ConflictError(DomainError):
    """Resource conflict (e.g., duplicate)."""
    pass


class ValidationError(DomainError):
    """Domain validation error."""
    pass


class AuthenticationError(DomainError):
    """Authentication failure."""

    def __init__(self, message: str = "Invalid credentials"):
        super().__init__(message)


class AuthorizationError(DomainError):
    """Authorization/permission failure."""

    def __init__(self, message: str = "Insufficient permissions"):
        super().__init__(message)


class RateLimitError(DomainError):
    """Rate limit exceeded."""

    def __init__(self, message: str = "Too many requests"):
        super().__init__(message)
