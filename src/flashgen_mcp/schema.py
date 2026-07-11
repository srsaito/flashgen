from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CardRequest(BaseModel):
    japanese: str = ""
    english: str = ""
    notes: str = ""
    tags: list[str] | None = None
    deck: str | None = None
    japanese_tts: str = ""
    japanese_prompt: str = ""
    english_prompt: str = ""
    japanese_prompt_tts: str = ""
    tts_provider: str | None = None
    tts_model: str | None = None
    card_type: Literal["standard", "dialog_response"] = "standard"

    @model_validator(mode="after")
    def check_constraints(self) -> "CardRequest":
        if not self.japanese.strip() and not self.english.strip():
            raise ValueError(
                "at least one of 'japanese' or 'english' must be provided"
            )
        if (self.tts_provider is None) != (self.tts_model is None):
            raise ValueError(
                "'tts_provider' and 'tts_model' must be provided together or both omitted"
            )
        if self.card_type == "dialog_response" and not self.japanese_prompt.strip():
            raise ValueError(
                "card_type 'dialog_response' requires a non-empty 'japanese_prompt'"
            )
        return self


class SearchNotesRequest(BaseModel):
    query: str = ""
    deck: str | None = None
    limit: int = Field(default=25, ge=1, le=100)
    match_field: Literal["japanese_tts", "japanese", "english", "any"] = "japanese_tts"

    @model_validator(mode="after")
    def check_constraints(self) -> "SearchNotesRequest":
        if not self.query.strip() and not (self.deck or "").strip():
            raise ValueError("provide 'query' and/or 'deck'")
        return self


class GetNoteRequest(BaseModel):
    note_id: int


class DeleteNotesRequest(BaseModel):
    note_ids: list[int] = Field(min_length=1)


class UpdateNoteRequest(BaseModel):
    note_id: int
    fields: dict[str, str] | None = None
    tags: list[str] | None = None
    tts_provider: str | None = None
    tts_model: str | None = None

    @model_validator(mode="after")
    def check_constraints(self) -> "UpdateNoteRequest":
        if not self.fields and self.tags is None:
            raise ValueError("provide 'fields' and/or 'tags' to update")
        if (self.tts_provider is None) != (self.tts_model is None):
            raise ValueError(
                "'tts_provider' and 'tts_model' must be provided together or both omitted"
            )
        return self


class CardResult(BaseModel):
    status: str
    note_id: int
    deck: str
    model: str
    card_type: str = "standard"
    japanese: str
    english: str
    notes: str
    tags: list[str]
    tts_provider: str
    tts_model: str
    japanese_tts: str
    audio_file: str
    local_audio_path: str
    japanese_prompt: str | None = None
    english_prompt: str | None = None
    japanese_prompt_tts: str | None = None
    audio_prompt_file: str | None = None
