use lancedb::connect;
use lancedb::connection::Connection;
use lancedb::table::Table;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::sync::Arc;
use tokio::sync::Mutex;
use once_cell::sync::Lazy;
use arrow_array::{RecordBatch, StringArray, TimestampMillisecondArray};
use arrow_schema::{DataType, Field, Schema, TimeUnit};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Serialize, Deserialize)]
pub struct Activity {
    pub id: String,
    pub content: String,
    pub timestamp: i64,
    pub metadata: String,
}

pub struct SemanticStore {
    db: Arc<Mutex<Option<Connection>>>,
}

impl SemanticStore {
    pub fn new() -> Self {
        Self {
            db: Arc::new(Mutex::new(None)),
        }
    }

    pub async fn init(&self, uri: &str) -> Result<(), String> {
        let conn = connect(uri).execute().await
            .map_err(|e| format!("Failed to connect to LanceDB: {}", e))?;
        let mut db = self.db.lock().await;
        *db = Some(conn);
        Ok(())
    }

    pub async fn add_activity(&self, content: String, metadata: String) -> Result<(), String> {
        let db_lock = self.db.lock().await;
        let db = db_lock.as_ref().ok_or("Database not initialized")?;
        
        let schema = Arc::new(Schema::new(vec![
            Field::new("content", DataType::Utf8, false),
            Field::new("metadata", DataType::Utf8, false),
            Field::new("timestamp", DataType::Timestamp(TimeUnit::Millisecond, None), false),
        ]));

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_millis() as i64;

        let batch = RecordBatch::try_new(
            schema.clone(),
            vec![
                Arc::new(StringArray::from(vec![content])),
                Arc::new(StringArray::from(vec![metadata])),
                Arc::new(TimestampMillisecondArray::from(vec![now])),
            ],
        ).map_err(|e| format!("Failed to create record batch: {}", e))?;

        // In a real implementation, we'd open the 'activity' table and append the batch.
        Ok(())
    }
}

static SEMANTIC_STORE: Lazy<SemanticStore> = Lazy::new(|| SemanticStore::new());

pub async fn init_semantic(uri: String) -> Result<(), String> {
    SEMANTIC_STORE.init(&uri).await
}

pub async fn add_activity(content: String, metadata: String) -> Result<(), String> {
    SEMANTIC_STORE.add_activity(content, metadata).await
}
