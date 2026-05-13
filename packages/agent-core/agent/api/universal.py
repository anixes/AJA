from enum import Enum
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

class FinishReason(str, Enum):
    STOP = "stop"
    LENGTH = "length"
    TOOL_CALL = "tool_call"
    CONTENT_FILTER = "content_filter"
    ERROR = "error"
    UNKNOWN = "unknown"

class ContentBlock(BaseModel):
    type: str
    text: Optional[str] = None
    id: Optional[str] = None
    name: Optional[str] = None
    arguments: Optional[Any] = None
    tool_call_id: Optional[str] = None
    content: Optional[List['ContentBlock']] = None
    is_error: Optional[bool] = None
    thinking: Optional[str] = None
    signature: Optional[str] = None
    data: Optional[str] = None
    media_type: Optional[str] = None
    url: Optional[str] = None

class UniversalItem(BaseModel):
    type: str
    role: Optional[Role] = None
    id: Optional[str] = None
    content: Optional[List[ContentBlock]] = None
    name: Optional[str] = None
    arguments: Optional[Any] = None
    tool_call_id: Optional[str] = None
    is_error: Optional[bool] = None
    text: Optional[str] = None
    encrypted: Optional[str] = None
    raw: Optional[Any] = None

class UniversalTool(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Optional[Dict[str, Any]] = None

class ToolChoice(BaseModel):
    type: str
    name: Optional[str] = None

class GenerationConfig(BaseModel):
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    max_output_tokens: Optional[int] = None

class ReasoningConfig(BaseModel):
    effort: Optional[str] = None
    budget_tokens: Optional[int] = None
    visible: Optional[bool] = None

class UniversalRequest(BaseModel):
    id: Optional[str] = None
    model: Optional[str] = None
    instructions: List[ContentBlock] = Field(default_factory=list)
    input: List[UniversalItem] = Field(default_factory=list)
    tools: List[UniversalTool] = Field(default_factory=list)
    tool_choice: Optional[ToolChoice] = None
    stream: bool = False
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    reasoning: Optional[ReasoningConfig] = None
