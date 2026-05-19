use pyo3::prelude::*;
use pyo3::exceptions::PyValueError;
use tiktoken_rs::cl100k_base;
use arrow::array::{StringArray, Array};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use arrow_ipc::writer::FileWriter;
use arrow_ipc::reader::FileReader;
use std::fs::File;
use std::sync::Arc;
use serde_json::Value;

/// High-performance token counter using tiktoken-rs (cl100k_base for OpenAI models)
#[pyfunction]
fn count_tokens(text: &str) -> PyResult<usize> {
    let bpe = cl100k_base().map_err(|e| PyValueError::new_err(format!("Failed to load tokenizer: {}", e)))?;
    let tokens = bpe.encode_with_special_tokens(text);
    Ok(tokens.len())
}

/// Serialize a baton JSON string into an Arrow IPC file on disk.
#[pyfunction]
fn write_baton_ipc(path: &str, json_data: &str) -> PyResult<()> {
    // Parse JSON
    let parsed: Value = serde_json::from_str(json_data)
        .map_err(|e| PyValueError::new_err(format!("Invalid JSON: {}", e)))?;

    // We will extract common fields and dump the rest as a JSON string payload.
    let objective = parsed.get("objective").and_then(|v| v.as_str()).unwrap_or("");
    let status = parsed.get("status").and_then(|v| v.as_str()).unwrap_or("pending");
    let stage = parsed.get("stage").and_then(|v| v.as_str()).unwrap_or("init");
    let worker_stdout = parsed.get("worker_stdout").and_then(|v| v.as_str()).unwrap_or("");
    let error_msg = parsed.get("error").and_then(|v| v.as_str()).unwrap_or("");
    
    // Everything else stays as a payload
    let mut payload_obj = parsed.clone();
    if let Value::Object(ref mut map) = payload_obj {
        map.remove("objective");
        map.remove("status");
        map.remove("stage");
        map.remove("worker_stdout");
        map.remove("error");
    }
    let payload_str = serde_json::to_string(&payload_obj).unwrap_or_else(|_| "{}".to_string());

    let schema = Schema::new(vec![
        Field::new("objective", DataType::Utf8, false),
        Field::new("status", DataType::Utf8, false),
        Field::new("stage", DataType::Utf8, false),
        Field::new("worker_stdout", DataType::Utf8, false),
        Field::new("error", DataType::Utf8, false),
        Field::new("payload", DataType::Utf8, false),
    ]);

    let batch = RecordBatch::try_new(
        Arc::new(schema),
        vec![
            Arc::new(StringArray::from(vec![objective])),
            Arc::new(StringArray::from(vec![status])),
            Arc::new(StringArray::from(vec![stage])),
            Arc::new(StringArray::from(vec![worker_stdout])),
            Arc::new(StringArray::from(vec![error_msg])),
            Arc::new(StringArray::from(vec![payload_str])),
        ],
    ).map_err(|e| PyValueError::new_err(format!("Arrow RecordBatch error: {}", e)))?;

    let file = File::create(path).map_err(|e| PyValueError::new_err(format!("File create error: {}", e)))?;
    let mut writer = FileWriter::try_new(file, batch.schema().as_ref())
        .map_err(|e| PyValueError::new_err(format!("FileWriter error: {}", e)))?;

    writer.write(&batch).map_err(|e| PyValueError::new_err(format!("Write error: {}", e)))?;
    writer.finish().map_err(|e| PyValueError::new_err(format!("Finish error: {}", e)))?;

    Ok(())
}

/// Deserialize an Arrow IPC file back into a JSON string.
#[pyfunction]
fn read_baton_ipc(path: &str) -> PyResult<String> {
    let file = File::open(path).map_err(|e| PyValueError::new_err(format!("File open error: {}", e)))?;
    let mut reader = FileReader::try_new(file, None)
        .map_err(|e| PyValueError::new_err(format!("FileReader error: {}", e)))?;

    if let Some(batch_result) = reader.next() {
        let batch = batch_result.map_err(|e| PyValueError::new_err(format!("Batch read error: {}", e)))?;
        
        let objective_arr = batch.column(0).as_any().downcast_ref::<StringArray>().unwrap();
        let status_arr = batch.column(1).as_any().downcast_ref::<StringArray>().unwrap();
        let stage_arr = batch.column(2).as_any().downcast_ref::<StringArray>().unwrap();
        let stdout_arr = batch.column(3).as_any().downcast_ref::<StringArray>().unwrap();
        let error_arr = batch.column(4).as_any().downcast_ref::<StringArray>().unwrap();
        let payload_arr = batch.column(5).as_any().downcast_ref::<StringArray>().unwrap();

        if batch.num_rows() > 0 {
            let mut result: Value = serde_json::from_str(payload_arr.value(0))
                .unwrap_or_else(|_| serde_json::json!({}));
            
            if let Value::Object(ref mut map) = result {
                map.insert("objective".to_string(), Value::String(objective_arr.value(0).to_string()));
                map.insert("status".to_string(), Value::String(status_arr.value(0).to_string()));
                map.insert("stage".to_string(), Value::String(stage_arr.value(0).to_string()));
                map.insert("worker_stdout".to_string(), Value::String(stdout_arr.value(0).to_string()));
                map.insert("error".to_string(), Value::String(error_arr.value(0).to_string()));
            }

            return Ok(serde_json::to_string(&result).unwrap());
        }
    }

    Ok("{}".to_string())
}

#[pyfunction]
fn init_semantic(db_path: &str) -> PyResult<()> {
    std::fs::create_dir_all(db_path)
        .map_err(|e| PyValueError::new_err(format!("Failed to create database directory: {}", e)))?;
    println!("AgentX Native: Initialized semantic vector store folder at {}", db_path);
    Ok(())
}

/// Translate OpenAI/Generic request format to Anthropic format
#[pyfunction]
fn translate_to_anthropic(request_json: &str) -> PyResult<String> {
    let mut parsed: Value = serde_json::from_str(request_json)
        .map_err(|e| PyValueError::new_err(format!("Invalid JSON request in translate: {}", e)))?;
    
    let model = parsed.get("model").and_then(|v| v.as_str()).unwrap_or("claude-3-5-sonnet");
    
    let messages = if let Some(input) = parsed.get("input").and_then(|v| v.as_array()) {
        let mut mapped_msgs = Vec::new();
        for msg in input {
            let role = msg.get("role").and_then(|v| v.as_str()).unwrap_or("user");
            let content = msg.get("content").cloned().unwrap_or(Value::Array(vec![]));
            
            mapped_msgs.push(serde_json::json!({
                "role": role,
                "content": content
            }));
        }
        mapped_msgs
    } else {
        vec![]
    };
    
    let anthropic_req = serde_json::json!({
        "model": model,
        "messages": messages
    });
    
    Ok(anthropic_req.to_string())
}

/// Serializes handover baton state into Arrow format (5-arg format)
#[pyfunction]
fn write_baton(path: &str, objective: &str, run_id: &str, history_json: &str, metadata_json: &str) -> PyResult<()> {
    let schema = Schema::new(vec![
        Field::new("objective", DataType::Utf8, false),
        Field::new("run_id", DataType::Utf8, false),
        Field::new("history_json", DataType::Utf8, false),
        Field::new("metadata_json", DataType::Utf8, false),
    ]);

    let batch = RecordBatch::try_new(
        Arc::new(schema),
        vec![
            Arc::new(StringArray::from(vec![objective])),
            Arc::new(StringArray::from(vec![run_id])),
            Arc::new(StringArray::from(vec![history_json])),
            Arc::new(StringArray::from(vec![metadata_json])),
        ],
    ).map_err(|e| PyValueError::new_err(format!("Arrow RecordBatch error in write_baton: {}", e)))?;

    let file = File::create(path).map_err(|e| PyValueError::new_err(format!("File create error in write_baton: {}", e)))?;
    let mut writer = FileWriter::try_new(file, batch.schema().as_ref())
        .map_err(|e| PyValueError::new_err(format!("FileWriter error in write_baton: {}", e)))?;

    writer.write(&batch).map_err(|e| PyValueError::new_err(format!("Write error in write_baton: {}", e)))?;
    writer.finish().map_err(|e| PyValueError::new_err(format!("Finish error in write_baton: {}", e)))?;

    Ok(())
}

/// Deserializes handover baton state from Arrow format (returns Python dict)
#[pyfunction]
fn read_baton(py: Python<'_>, path: &str) -> PyResult<PyObject> {
    let file = File::open(path).map_err(|e| PyValueError::new_err(format!("File open error in read_baton: {}", e)))?;
    let mut reader = FileReader::try_new(file, None)
        .map_err(|e| PyValueError::new_err(format!("FileReader error in read_baton: {}", e)))?;

    if let Some(batch_result) = reader.next() {
        let batch = batch_result.map_err(|e| PyValueError::new_err(format!("Batch read error in read_baton: {}", e)))?;
        
        if batch.num_rows() > 0 {
            let objective_arr = batch.column(0).as_any().downcast_ref::<StringArray>().unwrap();
            let run_id_arr = batch.column(1).as_any().downcast_ref::<StringArray>().unwrap();
            let history_json_arr = batch.column(2).as_any().downcast_ref::<StringArray>().unwrap();
            let metadata_json_arr = batch.column(3).as_any().downcast_ref::<StringArray>().unwrap();

            let dict = pyo3::types::PyDict::new(py);
            dict.set_item("objective", objective_arr.value(0))?;
            dict.set_item("run_id", run_id_arr.value(0))?;
            dict.set_item("history_json", history_json_arr.value(0))?;
            dict.set_item("metadata_json", metadata_json_arr.value(0))?;

            return Ok(dict.to_object(py));
        }
    }

    let dict = pyo3::types::PyDict::new(py);
    dict.set_item("objective", "")?;
    dict.set_item("run_id", "")?;
    dict.set_item("history_json", "[]")?;
    dict.set_item("metadata_json", "{}")?;
    Ok(dict.to_object(py))
}

/// Dynamic context manager that parses turn token counts using cl100k_base
/// and identifies structural middle turns for adaptive summary compression.
#[pyclass]
struct PyTrajectoryManager {
    model_id: String,
}

#[pymethods]
impl PyTrajectoryManager {
    #[new]
    fn new(model_id: String) -> Self {
        PyTrajectoryManager { model_id }
    }

    fn analyze(&self, messages_json: &str, limit: usize, head: usize, tail: usize) -> PyResult<String> {
        let messages: Vec<serde_json::Value> = serde_json::from_str(messages_json)
            .map_err(|e| PyValueError::new_err(format!("Invalid JSON in analyze: {}", e)))?;
        
        let bpe = cl100k_base().map_err(|e| PyValueError::new_err(format!("Failed to load tokenizer: {}", e)))?;
        let mut total_tokens = 0;
        
        for msg in &messages {
            let mut text = String::new();
            if let Some(content) = msg.get("content") {
                if let Some(s) = content.as_str() {
                    text = s.to_string();
                } else if let Some(arr) = content.as_array() {
                    for block in arr {
                        if let Some(t) = block.get("text").and_then(|v| v.as_str()) {
                            text.push_str(t);
                        } else if let Some(t) = block.as_str() {
                            text.push_str(t);
                        }
                    }
                }
            }
            total_tokens += bpe.encode_with_special_tokens(&text).len();
        }
        
        let should_compress = total_tokens > limit;
        let mut compress_start = 0;
        let mut compress_end = 0;
        
        if should_compress && messages.len() > (head + tail) {
            compress_start = head;
            compress_end = messages.len() - tail;
        }
        
        let response = serde_json::json!({
            "total_tokens": total_tokens,
            "should_compress": should_compress,
            "compress_start": compress_start,
            "compress_end": compress_end,
        });
        
        Ok(response.to_string())
    }
}

/// The AgentX Native Python module
#[pymodule]
fn agentx_native(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(count_tokens, m)?)?;
    m.add_function(wrap_pyfunction!(write_baton_ipc, m)?)?;
    m.add_function(wrap_pyfunction!(read_baton_ipc, m)?)?;
    m.add_function(wrap_pyfunction!(init_semantic, m)?)?;
    m.add_function(wrap_pyfunction!(translate_to_anthropic, m)?)?;
    m.add_function(wrap_pyfunction!(write_baton, m)?)?;
    m.add_function(wrap_pyfunction!(read_baton, m)?)?;
    m.add_class::<PyTrajectoryManager>()?;
    Ok(())
}
