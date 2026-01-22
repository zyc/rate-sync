"""Rate limiter specific exceptions."""


class RateLimiterError(Exception):
    """Base exception for rate limiter errors."""


class RateLimiterNotConfiguredError(RateLimiterError):
    """Exception raised when rate limiter was not configured."""

    def __init__(self, name: str) -> None:
        """Initialize the exception.

        Args:
            name: Name of the rate limiter that wasn't configured
        """
        self.name = name
        super().__init__(
            f"Rate limiter '{name}' was not configured. "
            f"Call configure_rate_limiter('{name}', ...) first."
        )


class RateLimiterNotInitializedError(RateLimiterError):
    """Exception raised when rate limiter was not initialized."""

    def __init__(self, name: str | None = None) -> None:
        """Initialize the exception.

        Args:
            name: Optional name of the rate limiter
        """
        self.name = name
        if name:
            message = (
                f"Rate limiter '{name}' was not initialized. "
                f"Call await initialize_rate_limiter('{name}') first."
            )
        else:
            message = "Rate limiter was not initialized. Call initialize() first."
        super().__init__(message)


class RateLimiterAcquisitionError(RateLimiterError):
    """Exception raised when unable to acquire slot."""

    def __init__(
        self, message: str, group_id: str | None = None, attempts: int | None = None
    ) -> None:
        """Initialize the exception.

        Args:
            message: Error message
            group_id: Optional ID of the group that tried to acquire
            attempts: Optional number of attempts made
        """
        self.group_id = group_id
        self.attempts = attempts
        super().__init__(message)


class RateLimiterAlreadyConfiguredError(RateLimiterError):
    """Exception raised when rate limiter was already configured."""

    def __init__(self, name: str) -> None:
        """Initialize the exception.

        Args:
            name: Name of the already configured rate limiter
        """
        self.name = name
        super().__init__(
            f"Rate limiter '{name}' was already configured. "
            f"To reconfigure, remove the existing configuration first."
        )


class ConfigValidationError(RateLimiterError):
    """Exception raised when configuration validation fails."""

    def __init__(
        self,
        message: str,
        field: str | None = None,
        expected: str | None = None,
        received: str | None = None,
    ) -> None:
        """Initialize the exception.

        Args:
            message: Error message
            field: Field name that failed validation
            expected: Expected value or type
            received: Received value or type
        """
        self.field = field
        self.expected = expected
        self.received = received

        full_message = message
        if field and expected and received:
            full_message = f"{message} (field='{field}', expected={expected}, received={received})"

        super().__init__(full_message)


class StoreNotFoundError(RateLimiterError):
    """Exception raised when a store is not found."""

    def __init__(self, store_id: str) -> None:
        """Initialize the exception.

        Args:
            store_id: ID of the store that was not found
        """
        self.store_id = store_id
        super().__init__(
            f"Store '{store_id}' not found. Configure it first using configure_store()."
        )


class LimiterNotFoundError(RateLimiterError):
    """Exception raised when a limiter is not found."""

    def __init__(self, limiter_id: str) -> None:
        """Initialize the exception.

        Args:
            limiter_id: ID of the limiter that was not found
        """
        self.limiter_id = limiter_id
        super().__init__(
            f"Limiter '{limiter_id}' not found. "
            f"Configure it first using configure_limiter() or load_config()."
        )
