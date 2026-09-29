"""Actionable kernel errors shared by HTTP and agent command callers."""


class GraphError(ValueError):
    def __init__(self, code, message, *, status_code=422, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail or {}

    def as_dict(self):
        return {"code": self.code, "message": self.message, "retryable": self.code == "revision_conflict", **self.detail}
