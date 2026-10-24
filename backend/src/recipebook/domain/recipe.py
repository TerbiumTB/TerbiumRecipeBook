from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True, allow_inf_nan=False)

Text = Annotated[str, Field(min_length=1)]
LocalId = Annotated[str, Field(pattern=r'^[a-z][a-z0-9_-]{0,63}$')]
Unit = Literal['g','kg','ml','l','piece','tsp','tbsp','pinch','clove','bunch']

class Exact(Model):
    kind: Literal['exact']
    value: float = Field(gt=0)
    unit: Unit

class Range(Model):
    kind: Literal['range']
    min: float = Field(gt=0)
    max: float = Field(gt=0)
    unit: Unit
    @model_validator(mode='after')
    def ordered(self):
        if self.min > self.max: raise ValueError('Минимум не может быть больше максимума')
        return self

class FreeQuantity(Model):
    kind: Literal['to_taste','as_needed']

class Ingredient(Model):
    id: LocalId
    name: Text
    quantity: Exact | Range | FreeQuantity
    preparation: str | None
    group: str | None
    optional: bool

class Step(Model):
    id: LocalId
    title: Annotated[str, Field(min_length=1,max_length=100)]
    instruction: Text
    ingredientIds: list[LocalId]
    timerSeconds: int | None = Field(ge=1)

class CookingTime(Model):
    totalMinutes: int = Field(ge=1)
    activeMinutes: int = Field(ge=1)
    @model_validator(mode='after')
    def ordered(self):
        if self.activeMinutes > self.totalMinutes: raise ValueError('Активное время больше общего')
        return self

class RecipeDraft(Model):
    title: Annotated[str, Field(min_length=1,max_length=160)]
    servings: int = Field(ge=1,le=100)
    time: CookingTime
    ingredients: list[Ingredient] = Field(min_length=1,max_length=60)
    steps: list[Step] = Field(min_length=1,max_length=40)
    @model_validator(mode='after')
    def references(self):
        ids = [i.id for i in self.ingredients]
        if len(ids) != len(set(ids)): raise ValueError('Повторяются ID ингредиентов')
        steps = [s.id for s in self.steps]
        if len(steps) != len(set(steps)): raise ValueError('Повторяются ID шагов')
        for step in self.steps:
            if len(step.ingredientIds) != len(set(step.ingredientIds)) or not set(step.ingredientIds) <= set(ids):
                raise ValueError('Неверные ссылки на ингредиенты')
        return self

class RecipeContent(RecipeDraft):
    schemaVersion: Literal[1]
    userNotes: str

class Idea(Model):
    title: Text
    description: str

class ModelReply(Model):
    message: Text
    ideas: list[Idea] = Field(max_length=5)
    recipe: RecipeDraft | None
