"""业务异常：统一由全局异常处理器转为响应包络。"""


class AppError(Exception):
    def __init__(self, status_code: int, code: str, message: str, data=None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)
