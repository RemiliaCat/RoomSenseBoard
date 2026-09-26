'''HTTP 层共用的错误类型。'''

from __future__ import annotations


class ApiError(Exception):
    '''带 HTTP 状态码的业务错误。'''

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
