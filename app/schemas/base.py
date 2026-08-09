from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    """对外模型基类：DB snake_case ↔ API camelCase。"""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
