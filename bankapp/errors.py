class ApiError(Exception):
    """Raised anywhere in a request; rendered as JSON {error, field}."""

    def __init__(self, message, status=400, field=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.field = field
