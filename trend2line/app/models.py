"""Typed contracts. Models used as Gemini response schemas avoid dict-typed fields.

Process steps and categories are plain strings: their vocabulary lives in data/process_map.csv and is passed
to Gemini in the prompt; code validates the answer and flags anything outside the vocabulary.
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class ArticleChoice(BaseModel):
    title: str = Field(description="Exactly one of the candidate titles")
    reason: str


class SpecIngredient(BaseModel):
    name: str = Field(description="Common English ingredient name, as on an ingredient list")
    role: Literal["shell", "filling", "inclusion", "coating", "base", "topping", "other"] = "other"
    components: list[str] = Field(default_factory=list,
                                  description="For compound ingredients only: simple English sub-ingredients")


class ProductSpec(BaseModel):
    product_name: str = Field(description="Descriptive product name, no brands, no place names")
    category: str = Field(description="One of the given categories")
    format: str = Field(description="Short format, e.g. solid_bar, shell_and_fill, beverage, bakery, snack")
    hero_ingredient: str = Field(description="The single ingredient that defines the trend")
    ingredients: list[SpecIngredient]
    process_steps: list[str] = Field(description="Ordered steps, each from the given vocabulary")
    notes: str = ""


class Claim(BaseModel):
    text: str
    kind: Literal["numeric", "date", "process", "regulatory", "speculative"]
    evidence_ids: list[str] = Field(default_factory=list)
    field: Optional[str] = Field(default=None, description="Dot path into the cited evidence payload")
    value: Optional[float] = None
    date_value: Optional[str] = Field(default=None, description="YYYY-MM-DD")
    line: Optional[str] = None
    capability: Optional[str] = None
    capability_present: Optional[bool] = None
    rule_id: Optional[str] = None


class Plan(BaseModel):
    headline: str
    rationale: str
    actions: list[str]
    claims: list[Claim]


class LabelTerm(BaseModel):
    declaration: str = Field(description="Copied exactly from mandatory_declarations")
    local_term: str = Field(description="The term printed on the label, in the target language")


class LabelDraft(BaseModel):
    product_name_local: str
    ingredients_statement: str
    allergen_terms: list[LabelTerm]
    allergen_statement: str = Field(description="The 'Contains: ...' statement, with every local_term verbatim")
    back_translation: str = Field(description="English translation of both statements, for the reviewer")


class Answer(BaseModel):
    answer: str
    claims: list[Claim]
