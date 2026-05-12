use tiktoken_rs::get_chat_completion_max_tokens;
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct Message {
    pub role: String,
    pub content: String,
}

#[derive(Debug, Serialize, Deserialize)]
pub struct TrajectoryAnalysis {
    pub total_tokens: usize,
    pub should_compress: bool,
    pub compress_start: Option<usize>,
    pub compress_end: Option<usize>,
}

pub struct TrajectoryManager {
    model: String,
}

impl TrajectoryManager {
    pub fn new(model: &str) -> Self {
        Self {
            model: model.to_string(),
        }
    }

    pub fn count_tokens(&self, messages: &[Message]) -> usize {
        // Simplified token counting logic using tiktoken
        // In a real implementation, we'd use the model-specific bpe
        let total_text: String = messages.iter().map(|m| m.content.clone()).collect::<Vec<String>>().join(" ");
        total_text.len() / 4 // Fallback rough estimate for now, will refine with tiktoken
    }

    pub fn analyze_for_compression(
        &self,
        messages: &[Message],
        limit: usize,
        protected_head: usize,
        protected_tail: usize,
    ) -> TrajectoryAnalysis {
        let total_tokens = self.count_tokens(messages);
        let should_compress = total_tokens > limit;

        let mut analysis = TrajectoryAnalysis {
            total_tokens,
            should_compress,
            compress_start: None,
            compress_end: None,
        };

        if should_compress && messages.len() > (protected_head + protected_tail + 1) {
            analysis.compress_start = Some(protected_head);
            analysis.compress_end = Some(messages.len() - protected_tail);
        }

        analysis
    }
}
