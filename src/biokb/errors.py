"""可向调用方展示的业务错误，不携带服务端凭据和原始响应。"""


class KnowledgeError(Exception):
    status_code = 400
    code = "invalid_request"


class NotFoundError(KnowledgeError):
    status_code = 404
    code = "not_found"


class ConflictError(KnowledgeError):
    status_code = 409
    code = "conflict"


class ProviderError(KnowledgeError):
    status_code = 502
    code = "provider_error"


class IndexMismatchError(ConflictError):
    code = "index_mismatch"
