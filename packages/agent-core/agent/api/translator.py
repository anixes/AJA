import json
from typing import List, Dict, Any, Optional
from .universal import (
    UniversalRequest, UniversalItem, ContentBlock, 
    Role, FinishReason, GenerationConfig
)

class AnthropicTranslator:
    @staticmethod
    def role_to_anthropic(role: Role) -> str:
        if role in [Role.SYSTEM, Role.USER, Role.TOOL]:
            return "user"
        return "assistant"

    @staticmethod
    def finish_from_anthropic(reason: Optional[str]) -> FinishReason:
        mapping = {
            "end_turn": FinishReason.STOP,
            "stop_sequence": FinishReason.STOP,
            "max_tokens": FinishReason.LENGTH,
            "tool_use": FinishReason.TOOL_CALL,
        }
        return mapping.get(reason, FinishReason.UNKNOWN)

    def encode_request(self, request: UniversalRequest) -> Dict[str, Any]:
        body = {}
        if request.model:
            body["model"] = request.model
        
        # Anthropic uses a separate system parameter
        if request.instructions:
            system_text = "\n".join([b.text for b in request.instructions if b.text])
            if system_text:
                body["system"] = system_text

        messages = []
        for item in request.input:
            if item.type == "message":
                messages.append({
                    "role": self.role_to_anthropic(item.role),
                    "content": self.encode_content(item.content)
                })
            elif item.type == "tool_call":
                # In Anthropic, tool calls are part of assistant messages
                messages.append({
                    "role": "assistant",
                    "content": [{
                        "type": "tool_use",
                        "id": item.id,
                        "name": item.name,
                        "input": item.arguments
                    }]
                })
            elif item.type == "tool_result":
                messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": item.tool_call_id,
                        "content": self.encode_content(item.content),
                        "is_error": item.is_error
                    }]
                })

        body["messages"] = messages
        
        if request.tools:
            body["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema
                } for t in request.tools
            ]

        if request.stream:
            body["stream"] = True

        # Generation config
        if request.generation.max_output_tokens:
            body["max_tokens"] = request.generation.max_output_tokens
        else:
            body["max_tokens"] = 4096 # Default for Anthropic

        if request.generation.temperature is not None:
            body["temperature"] = request.generation.temperature

        return body

    def encode_content(self, content: List[ContentBlock]) -> List[Dict[str, Any]]:
        encoded = []
        for block in content:
            if block.type == "text":
                encoded.append({"type": "text", "text": block.text})
            elif block.type == "image":
                encoded.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.media_type,
                        "data": block.data
                    }
                })
            # Add more types as needed
        return encoded

class OpenAiTranslator:
    @staticmethod
    def role_to_openai(role: Role) -> str:
        return role.value

    def encode_request(self, request: UniversalRequest) -> Dict[str, Any]:
        body = {
            "model": request.model or "gpt-4o",
            "messages": []
        }

        if request.instructions:
            body["messages"].append({
                "role": "system",
                "content": "\n".join([b.text for b in request.instructions if b.text])
            })

        for item in request.input:
            if item.type == "message":
                body["messages"].append({
                    "role": self.role_to_openai(item.role),
                    "content": self.encode_content(item.content)
                })
            elif item.type == "tool_call":
                # OpenAI handles tool calls in the assistant message
                # This logic would normally be grouped with the previous assistant message
                body["messages"].append({
                    "role": "assistant",
                    "tool_calls": [{
                        "id": item.id,
                        "type": "function",
                        "function": {
                            "name": item.name,
                            "arguments": json.dumps(item.arguments)
                        }
                    }]
                })
            elif item.type == "tool_result":
                body["messages"].append({
                    "role": "tool",
                    "tool_call_id": item.tool_call_id,
                    "content": self.encode_content_text(item.content)
                })

        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema
                    }
                } for t in request.tools
            ]

        if request.stream:
            body["stream"] = True

        if request.generation.temperature is not None:
            body["temperature"] = request.generation.temperature
        
        if request.generation.max_output_tokens:
            body["max_completion_tokens"] = request.generation.max_output_tokens

        return body

    def encode_content(self, content: List[ContentBlock]) -> List[Dict[str, Any]]:
        # For simplicity, returning just text or complex objects if images are present
        encoded = []
        for block in content:
            if block.type == "text":
                encoded.append({"type": "text", "text": block.text})
            elif block.type == "image":
                encoded.append({
                    "type": "image_url",
                    "image_url": {"url": block.url or f"data:{block.media_type};base64,{block.data}"}
                })
        return encoded

    def encode_content_text(self, content: List[ContentBlock]) -> str:
        return "\n".join([b.text for b in content if b.text])
