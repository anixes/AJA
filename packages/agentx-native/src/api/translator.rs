use crate::api::universal::{ContentBlock, Role, UniversalRequest, UniversalItem};
use serde_json::{json, Value, Map};

pub struct AnthropicTranslator;

impl AnthropicTranslator {
    pub fn role_to_anthropic(role: &Role) -> &'static str {
        match role {
            Role::System | Role::User | Role::Tool => "user",
            Role::Assistant => "assistant",
        }
    }

    pub fn encode_request(request: &UniversalRequest) -> Value {
        let mut body = Map::new();
        if let Some(model) = &request.model {
            body.insert("model".to_string(), json!(model));
        }

        if !request.instructions.is_empty() {
            let system_text: String = request.instructions.iter()
                .filter_map(|b| b.text.as_ref())
                .cloned()
                .collect::<Vec<_>>()
                .join("\n");
            if !system_text.is_empty() {
                body.insert("system".to_string(), json!(system_text));
            }
        }

        let mut messages = Vec::new();
        for item in &request.input {
            match item {
                UniversalItem::Message { role, content, .. } => {
                    messages.push(json!({
                        "role": Self::role_to_anthropic(role),
                        "content": Self::encode_content(content)
                    }));
                }
                UniversalItem::ToolCall { id, name, arguments } => {
                    messages.push(json!({
                        "role": "assistant",
                        "content": [{
                            "type": "tool_use",
                            "id": id,
                            "name": name,
                            "input": arguments
                        }]
                    }));
                }
                UniversalItem::ToolResult { tool_call_id, content, is_error } => {
                    messages.push(json!({
                        "role": "user",
                        "content": [{
                            "type": "tool_result",
                            "tool_use_id": tool_call_id,
                            "content": Self::encode_content(content),
                            "is_error": is_error.unwrap_or(false)
                        }]
                    }));
                }
            }
        }

        body.insert("messages".to_string(), json!(messages));

        if !request.tools.is_empty() {
            body.insert("tools".to_string(), json!(request.tools.iter().map(|t| {
                json!({
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema
                })
            }).collect::<Vec<_>>()));
        }

        body.insert("max_tokens".to_string(), json!(request.generation.max_output_tokens.unwrap_or(4096)));
        if let Some(temp) = request.generation.temperature {
            body.insert("temperature".to_string(), json!(temp));
        }
        if request.stream {
            body.insert("stream".to_string(), json!(true));
        }

        Value::Object(body)
    }

    fn encode_content(content: &[ContentBlock]) -> Value {
        let mut blocks = Vec::new();
        for block in content {
            if block.r#type == "text" {
                if let Some(text) = &block.text {
                    blocks.push(json!({ "type": "text", "text": text }));
                }
            } else if block.r#type == "image" {
                blocks.push(json!({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": block.media_type,
                        "data": block.data
                    }
                }));
            }
        }
        json!(blocks)
    }
}
