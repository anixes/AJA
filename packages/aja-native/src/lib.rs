use pyo3::exceptions::{PyIOError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyDict;
use tiktoken_rs::CoreBPE;
use arrow::array::{StringArray, Array};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use arrow_ipc::writer::FileWriter;
use arrow_ipc::reader::FileReader;
use std::fs::File;
use std::sync::{Arc, OnceLock};
use serde_json::Value;
use base64::Engine;

/// SHA256 of the vendored cl100k_base blob (vendor/cl100k_base.tiktoken.sha256).
const VENDOR_BLOB_SHA256: &str = "223921b76ee99bde995b7ff738513eef100fb51d18c93597a113bcffe865b2a7";

/// Lazily-initialized offline tokenizer. Stores Result inside the OnceLock so
/// a corrupted vendored blob surfaces as a Python exception instead of panicking.
fn get_bpe() -> Result<&'static CoreBPE, String> {
    static BPE: OnceLock<Result<CoreBPE, String>> = OnceLock::new();
    let init = BPE.get_or_init(|| {
        let cl100k_base = include_str!("../vendor/cl100k_base.tiktoken");

        // One-time integrity check against the sidecar checksum.
        use sha2::{Digest, Sha256};
        let digest = Sha256::digest(cl100k_base.as_bytes());
        let hex: String = digest.iter().map(|b| format!("{:02x}", b)).collect();
        if hex != VENDOR_BLOB_SHA256 {
            return Err(format!(
                "Vendored tokenizer checksum mismatch: expected {}, got {}",
                VENDOR_BLOB_SHA256, hex
            ));
        }

        let mut encoder = rustc_hash::FxHashMap::default();
        for (i, line) in cl100k_base.lines().enumerate() {
            let mut parts = line.split(' ');
            let Some(raw) = parts.next() else {
                return Err(format!("Tokenizer blob line {}: missing token", i));
            };
            let Ok(token) = base64::engine::general_purpose::STANDARD.decode(raw) else {
                return Err(format!("Tokenizer blob line {}: base64 decode failed", i));
            };
            let Some(rank_raw) = parts.next() else {
                return Err(format!("Tokenizer blob line {}: missing rank", i));
            };
            let Ok(rank) = rank_raw.parse::<u32>() else {
                return Err(format!("Tokenizer blob line {}: invalid rank", i));
            };
            encoder.insert(token, rank);
        }

        let mut special_tokens = rustc_hash::FxHashMap::default();
        special_tokens.insert(String::from("<|endoftext|>"), 100257);
        special_tokens.insert(String::from("<|fim_prefix|>"), 100258);
        special_tokens.insert(String::from("<|fim_middle|>"), 100259);
        special_tokens.insert(String::from("<|fim_suffix|>"), 100260);
        special_tokens.insert(String::from("<|endofprompt|>"), 100276);

        let pattern = "(?i:'s|'t|'re|'ve|'m|'ll|'d)|[^\\r\\n\\p{L}\\p{N}]?\\p{L}+|\\p{N}{1,3}| ?[^\\s\\p{L}\\p{N}]+[\\r\\n]*|\\s*[\\r\\n]+|\\s+(?!\\S)|\\s+";

        match CoreBPE::new(encoder, special_tokens, pattern) {
            Ok(bpe) => Ok(bpe),
            Err(e) => Err(format!("CoreBPE creation failed: {}", e)),
        }
    });
    match init {
        Ok(bpe) => Ok(bpe),
        Err(e) => Err(e.clone()),
    }
}

fn bpe_or_err() -> PyResult<&'static CoreBPE> {
    get_bpe().map_err(|e| PyValueError::new_err(format!("Failed to load tokenizer: {}", e)))
}

/// High-performance token counter using tiktoken-rs (cl100k_base for OpenAI models)
#[pyfunction]
fn count_tokens(py: Python<'_>, text: &str) -> PyResult<usize> {
    let bpe = bpe_or_err()?;
    // GIL-free: tokenization is pure CPU work on Send+Sync static state.
    py.detach(|| {
        let tokens = bpe.encode_with_special_tokens(text);
        Ok(tokens.len())
    })
}

/// Batch version of token counter to reduce boundary-crossing overhead
#[pyfunction]
fn count_tokens_batch<'py>(py: Python<'py>, texts: Vec<String>) -> PyResult<Vec<usize>> {
    let bpe = bpe_or_err()?;
    py.detach(move || {
        let mut results = Vec::with_capacity(texts.len());
        for text in &texts {
            let tokens = bpe.encode_with_special_tokens(text);
            results.push(tokens.len());
        }
        Ok(results)
    })
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

    let file = File::create(path)
        .map_err(|e| PyIOError::new_err(format!("File create error: {}", e)))?;
    let mut writer = FileWriter::try_new(file, batch.schema().as_ref())
        .map_err(|e| PyIOError::new_err(format!("FileWriter error: {}", e)))?;

    writer.write(&batch).map_err(|e| PyIOError::new_err(format!("Write error: {}", e)))?;
    writer.finish().map_err(|e| PyIOError::new_err(format!("Finish error: {}", e)))?;

    Ok(())
}

/// Deserialize an Arrow IPC file back into a JSON string.
#[pyfunction]
fn read_baton_ipc(path: &str) -> PyResult<String> {
    const EXPECTED_COLUMNS: usize = 6;
    let corrupt = |what: &str| PyValueError::new_err(format!(
        "Unsupported or corrupted baton file: expected {} columns ({} failed)",
        EXPECTED_COLUMNS, what
    ));

    let file = File::open(path)
        .map_err(|e| PyIOError::new_err(format!("File open error: {}", e)))?;
    let mut reader = FileReader::try_new(file, None)
        .map_err(|e| PyValueError::new_err(format!("FileReader error: {}", e)))?;

    if let Some(batch_result) = reader.next() {
        let batch = batch_result
            .map_err(|e| PyValueError::new_err(format!("Batch read error: {}", e)))?;

        if batch.num_columns() != EXPECTED_COLUMNS {
            return Err(corrupt("column count"));
        }

        let objective_arr = batch.column(0).as_any().downcast_ref::<StringArray>()
            .ok_or_else(|| corrupt("objective"))?;
        let status_arr = batch.column(1).as_any().downcast_ref::<StringArray>()
            .ok_or_else(|| corrupt("status"))?;
        let stage_arr = batch.column(2).as_any().downcast_ref::<StringArray>()
            .ok_or_else(|| corrupt("stage"))?;
        let stdout_arr = batch.column(3).as_any().downcast_ref::<StringArray>()
            .ok_or_else(|| corrupt("worker_stdout"))?;
        let error_arr = batch.column(4).as_any().downcast_ref::<StringArray>()
            .ok_or_else(|| corrupt("error"))?;
        let payload_arr = batch.column(5).as_any().downcast_ref::<StringArray>()
            .ok_or_else(|| corrupt("payload"))?;

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

            let serialized = serde_json::to_string(&result)
                .map_err(|e| PyValueError::new_err(format!("Serialization error: {}", e)))?;
            return Ok(serialized);
        }
    }

    Ok("{}".to_string())
}

#[pyfunction]
fn init_semantic(db_path: &str) -> PyResult<()> {
    std::fs::create_dir_all(db_path)
        .map_err(|e| PyIOError::new_err(format!("Failed to create database directory: {}", e)))?;
    println!("AJA Native: Initialized semantic vector store folder at {}", db_path);
    Ok(())
}

/// Serializes handover baton state into Arrow format (5-arg format).
/// DEPRECATED: mission baton IO moved to Python pyarrow schema v2.
#[deprecated = "mission baton IO moved to Python pyarrow schema v2"]
#[allow(deprecated)]
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

    let file = File::create(path)
        .map_err(|e| PyIOError::new_err(format!("File create error in write_baton: {}", e)))?;
    let mut writer = FileWriter::try_new(file, batch.schema().as_ref())
        .map_err(|e| PyIOError::new_err(format!("FileWriter error in write_baton: {}", e)))?;

    writer.write(&batch).map_err(|e| PyIOError::new_err(format!("Write error in write_baton: {}", e)))?;
    writer.finish().map_err(|e| PyIOError::new_err(format!("Finish error in write_baton: {}", e)))?;

    Ok(())
}

/// Deserializes handover baton state from Arrow format (returns Python dict).
/// DEPRECATED: mission baton IO moved to Python pyarrow schema v2.
#[deprecated = "mission baton IO moved to Python pyarrow schema v2"]
#[allow(deprecated)]
#[pyfunction]
fn read_baton(py: Python<'_>, path: &str) -> PyResult<Py<PyDict>> {
    const EXPECTED_COLUMNS: usize = 4;
    let corrupt = |what: &str| PyValueError::new_err(format!(
        "Unsupported or corrupted baton file: expected {} columns ({} failed)",
        EXPECTED_COLUMNS, what
    ));

    let file = File::open(path)
        .map_err(|e| PyIOError::new_err(format!("File open error in read_baton: {}", e)))?;
    let mut reader = FileReader::try_new(file, None)
        .map_err(|e| PyValueError::new_err(format!("FileReader error in read_baton: {}", e)))?;

    if let Some(batch_result) = reader.next() {
        let batch = batch_result
            .map_err(|e| PyValueError::new_err(format!("Batch read error in read_baton: {}", e)))?;

        if batch.num_columns() != EXPECTED_COLUMNS {
            return Err(corrupt("column count"));
        }

        if batch.num_rows() > 0 {
            let objective_arr = batch.column(0).as_any().downcast_ref::<StringArray>()
                .ok_or_else(|| corrupt("objective"))?;
            let run_id_arr = batch.column(1).as_any().downcast_ref::<StringArray>()
                .ok_or_else(|| corrupt("run_id"))?;
            let history_json_arr = batch.column(2).as_any().downcast_ref::<StringArray>()
                .ok_or_else(|| corrupt("history_json"))?;
            let metadata_json_arr = batch.column(3).as_any().downcast_ref::<StringArray>()
                .ok_or_else(|| corrupt("metadata_json"))?;

            #[allow(deprecated)]
            {
                let dict = PyDict::new(py);
                dict.set_item("objective", objective_arr.value(0))?;
                dict.set_item("run_id", run_id_arr.value(0))?;
                dict.set_item("history_json", history_json_arr.value(0))?;
                dict.set_item("metadata_json", metadata_json_arr.value(0))?;
                return Ok(dict.unbind());
            }
        }
    }

    let dict = PyDict::new(py);
    dict.set_item("objective", "")?;
    dict.set_item("run_id", "")?;
    dict.set_item("history_json", "[]")?;
    dict.set_item("metadata_json", "{}")?;
    Ok(dict.unbind())
}

/// Dynamic context manager that parses turn token counts using cl100k_base
/// and identifies structural middle turns for adaptive summary compression.
///
/// Note: `analyze` intentionally returns a JSON *string*, not a dict — the
/// Python caller (libs/aja-core/aja/gateway/orchestrator.py ~line 159) runs
/// `json.loads()` on the result.
#[pyclass]
struct PyTrajectoryManager {
    #[allow(dead_code)]
    model_id: String,
}

#[pymethods]
impl PyTrajectoryManager {
    #[new]
    fn new(model_id: String) -> Self {
        PyTrajectoryManager { model_id }
    }

    fn analyze(&self, py: Python<'_>, messages_json: &str, limit: usize, head: usize, tail: usize) -> PyResult<String> {
        let messages: Vec<serde_json::Value> = serde_json::from_str(messages_json)
            .map_err(|e| PyValueError::new_err(format!("Invalid JSON in analyze: {}", e)))?;

        let bpe = bpe_or_err()?;
        // GIL-free: the tokenization loop is pure CPU on Send+Sync state.
        let analysis = py.detach(|| {
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

            serde_json::json!({
                "total_tokens": total_tokens,
                "should_compress": should_compress,
                "compress_start": compress_start,
                "compress_end": compress_end,
            })
        });

        Ok(analysis.to_string())
    }
}

/// The AJA Native Python module
#[pymodule]
fn aja_native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(count_tokens, m)?)?;
    m.add_function(wrap_pyfunction!(count_tokens_batch, m)?)?;
    m.add_function(wrap_pyfunction!(write_baton_ipc, m)?)?;
    m.add_function(wrap_pyfunction!(read_baton_ipc, m)?)?;
    m.add_function(wrap_pyfunction!(init_semantic, m)?)?;
    #[allow(deprecated)]
    {
        m.add_function(wrap_pyfunction!(write_baton, m)?)?;
        m.add_function(wrap_pyfunction!(read_baton, m)?)?;
    }
    m.add_class::<PyTrajectoryManager>()?;
    Ok(())
}
