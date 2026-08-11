"""通用格式化工具。"""


def mask_phone(phone: str) -> str:
    """手机号脱敏：138****8000。"""
    if len(phone) == 11:
        return phone[:3] + "****" + phone[-4:]
    return phone
