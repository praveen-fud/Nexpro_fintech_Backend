from pydantic import BaseModel, ConfigDict


def to_camel_case(snake: str) -> str:
    first, *rest = snake.split("_")
    return first + "".join(word.capitalize() for word in rest)


class CamelModel(BaseModel):
    """Base schema: Python stays snake_case, JSON over the wire is camelCase
    to match the TypeScript frontend's domain types exactly."""

    model_config = ConfigDict(alias_generator=to_camel_case, populate_by_name=True, from_attributes=True)


class PaginatedResponse(CamelModel):
    items: list
    total: int
    page: int
    page_size: int
