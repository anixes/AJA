use std::fs::File;
use std::sync::Arc;
use arrow_array::{StringArray, RecordBatch};
use arrow_schema::{DataType, Field, Schema};
use arrow_ipc::writer::FileWriter;
use arrow_ipc::reader::FileReader;
use serde_json::Value;

pub struct BatonState {
    pub objective: String,
    pub run_id: String,
    pub history_json: String,
    pub metadata_json: String,
}

pub fn write_baton_table(path: &str, state: BatonState) -> Result<(), Box<dyn std::error::Error>> {
    let schema = Arc::new(Schema::new(vec![
        Field::new("objective", DataType::Utf8, false),
        Field::new("run_id", DataType::Utf8, false),
        Field::new("history_blob", DataType::Utf8, false),
        Field::new("metadata_blob", DataType::Utf8, false),
    ]));

    let objective_arr = StringArray::from(vec![state.objective]);
    let run_id_arr = StringArray::from(vec![state.run_id]);
    let history_arr = StringArray::from(vec![state.history_json]);
    let metadata_arr = StringArray::from(vec![state.metadata_json]);

    let batch = RecordBatch::try_new(
        schema.clone(),
        vec![
            Arc::new(objective_arr),
            Arc::new(run_id_arr),
            Arc::new(history_arr),
            Arc::new(metadata_arr),
        ],
    )?;

    let file = File::create(path)?;
    let mut writer = FileWriter::try_new(file, &schema)?;
    writer.write(&batch)?;
    writer.finish()?;

    Ok(())
}

pub fn read_baton_table(path: &str) -> Result<BatonState, Box<dyn std::error::Error>> {
    let file = File::open(path)?;
    let reader = FileReader::try_new(file, None)?;
    
    // We only expect one batch for current batons
    for batch_result in reader {
        let batch = batch_result?;
        
        let objective = batch.column(0)
            .as_any()
            .downcast_ref::<StringArray>()
            .ok_or("Failed to downcast objective")?
            .value(0)
            .to_string();

        let run_id = batch.column(1)
            .as_any()
            .downcast_ref::<StringArray>()
            .ok_or("Failed to downcast run_id")?
            .value(0)
            .to_string();

        let history_json = batch.column(2)
            .as_any()
            .downcast_ref::<StringArray>()
            .ok_or("Failed to downcast history")?
            .value(0)
            .to_string();

        let metadata_json = batch.column(3)
            .as_any()
            .downcast_ref::<StringArray>()
            .ok_or("Failed to downcast metadata")?
            .value(0)
            .to_string();

        return Ok(BatonState {
            objective,
            run_id,
            history_json,
            metadata_json,
        });
    }

    Err("No data found in baton file".into())
}
