use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use uuid::Uuid;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ACPMessage {
    pub jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub method: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub params: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<Value>,
}

pub struct ACPBridge {
    pub handlers: Arc<Mutex<HashMap<String, Box<dyn Fn(Value) -> Value + Send + Sync>>>>,
}

impl ACPBridge {
    pub fn new() -> Self {
        Self {
            handlers: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub fn register_handler<F>(&self, method: &str, handler: F)
    where
        F: Fn(Value) -> Value + Send + Sync + 'static,
    {
        self.handlers.lock().unwrap().insert(method.to_string(), Box::new(handler));
    }

    pub fn create_request(&self, method: &str, params: Value) -> (String, String) {
        let id = Uuid::new_v4().to_string();
        let msg = json!({
            "jsonrpc": "2.0",
            "id": id,
            "method": method,
            "params": params
        });
        (id, serde_json::to_string(&msg).unwrap())
    }

    pub fn handle_message(&self, line: &str) -> Option<String> {
        let msg: ACPMessage = serde_json::from_str(line).ok()?;
        
        if let Some(method) = msg.method {
            let handlers = self.handlers.lock().unwrap();
            if let Some(handler) = handlers.get(&method) {
                let result = handler(msg.params.unwrap_or(Value::Null));
                if let Some(id) = msg.id {
                    let response = json!({
                        "jsonrpc": "2.0",
                        "id": id,
                        "result": result
                    });
                    return Some(serde_json::to_string(&response).unwrap());
                }
            }
        }
        None
    }
}
