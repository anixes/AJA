pub mod api;
pub mod trajectory;
pub mod baton;

use pyo3::prelude::*;
use crate::api::universal::UniversalRequest;
use crate::api::translator::AnthropicTranslator;
use crate::api::acp::ACPBridge;
use crate::api::semantic;
use once_cell::sync::Lazy;
use std::sync::Mutex;
use tokio::runtime::Runtime;

static BRIDGE: Lazy<Mutex<ACPBridge>> = Lazy::new(|| Mutex::new(ACPBridge::new()));
static TOKIO_RT: Lazy<Runtime> = Lazy::new(|| Runtime::new().unwrap());

#[pyfunction]
fn hello() -> PyResult<String> {
    Ok("Hello from AgentX Rust Core!".to_string())
}

#[pyfunction]
fn translate_to_anthropic(request_json: String) -> PyResult<String> {
    let request: UniversalRequest = serde_json::from_str(&request_json)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("Invalid JSON: {}", e)))?;
    
    let translated = AnthropicTranslator::encode_request(&request);
    Ok(serde_json::to_string(&translated).unwrap())
}

#[pyfunction]
fn init_semantic(uri: String) -> PyResult<()> {
    TOKIO_RT.block_on(semantic::init_semantic(uri))
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))
}

#[pyfunction]
fn add_activity(content: String, metadata: String) -> PyResult<()> {
    TOKIO_RT.block_on(semantic::add_activity(content, metadata))
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e))
}

#[pyfunction]
fn write_baton(path: String, objective: String, run_id: String, history_json: String, metadata_json: String) -> PyResult<()> {
    baton::write_baton_table(&path, baton::BatonState {
        objective,
        run_id,
        history_json,
        metadata_json,
    }).map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))
}

#[pyfunction]
fn read_baton(path: String) -> PyResult<PyObject> {
    let state = baton::read_baton_table(&path)
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(e.to_string()))?;
    
    Python::with_py( |py| {
        let dict = pyo3::types::PyDict::new_bound(py);
        dict.set_item("objective", state.objective)?;
        dict.set_item("run_id", state.run_id)?;
        dict.set_item("history_json", state.history_json)?;
        dict.set_item("metadata_json", state.metadata_json)?;
        Ok(dict.to_object(py))
    })
}

#[pymodule]
fn agentx_native(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(hello, m)?)?;
    m.add_function(wrap_pyfunction!(translate_to_anthropic, m)?)?;
    m.add_function(wrap_pyfunction!(init_semantic, m)?)?;
    m.add_function(wrap_pyfunction!(add_activity, m)?)?;
    m.add_function(wrap_pyfunction!(write_baton, m)?)?;
    m.add_function(wrap_pyfunction!(read_baton, m)?)?;
    
    #[pyclass]
    pub struct PyTrajectoryManager {
        inner: trajectory::TrajectoryManager,
    }

    #[pymethods]
    impl PyTrajectoryManager {
        #[new]
        fn new(model: String) -> Self {
            Self {
                inner: trajectory::TrajectoryManager::new(&model),
            }
        }

        fn analyze(&self, messages_json: String, limit: usize, head: usize, tail: usize) -> PyResult<String> {
            let messages: Vec<trajectory::Message> = serde_json::from_str(&messages_json)
                .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("JSON error: {}", e)))?;
            
            let analysis = self.inner.analyze_for_compression(&messages, limit, head, tail);
            Ok(serde_json::to_string(&analysis).unwrap())
        }
    }

    m.add_class::<PyTrajectoryManager>()?;
    Ok(())
}
