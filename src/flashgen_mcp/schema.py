from pydantic import BaseModel, model_validator


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
        return self


class CardResult(BaseModel):
    status: str
    note_id: int
    deck: str
    model: str
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
