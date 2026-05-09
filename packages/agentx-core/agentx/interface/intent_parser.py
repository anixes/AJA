import json
from typing import Dict, List, Any
import agentx.config
from agentx.llm import get_gateway_for_model

def parse_intent(message: str, history: List[Dict[str, Any]], system_state: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Convert natural language -> structured action.
    """
    model_name = agentx.config.AGENTX_PLANNER_MODEL
    gw, mapped_model = get_gateway_for_model(model_name)
    
    system_prompt = """You are the conversational intent parser for AgentX, an autonomous AI agent.
Analyze the user's message and the conversation history.
Determine if the user wants to:
1. "goal": Instruct the agent to perform a task, write code, or execute an action.
2. "question": Ask a general question, ask about system state, or chat.
3. "control": Pause, resume, stop, or change autonomy settings.

Respond ONLY in valid JSON format:
{
    "type": "goal" | "question" | "control",
    "goal": "Extracted goal description if type is 'goal', else null",
    "command": "pause/resume/status/auto_on/auto_off if type is 'control', else null",
    "response": "Conversational response to the user. E.g., 'Alright, starting that now.' or answer to their question.",
    "confidence": 0.0 to 1.0
}
Keep the conversational response natural, human-like, and concise. Do NOT be robotic.
If the request is ambiguous (e.g. 'deploy it'), ask a follow-up question via the 'response' field and set type to 'question'.
"""
    
    state_context = ""
    if system_state:
        state_context = "Current System State:\n" + json.dumps(system_state, indent=2) + "\n"

    # Format history
    chat_context = ""
    if history:
        chat_context = "Conversation History:\n" + "\n".join([f"{msg['role']}: {msg['content']}" for msg in history[-5:]])
        
    prompt = f"{state_context}\n{chat_context}\n\nUser Message: {message}\n\nExtract the intent in JSON format:"
    
    try:
        raw = gw.chat(model=mapped_model, prompt=prompt, system=system_prompt)
        # Strip markdown fences if present
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
            
        data = json.loads(raw)
        return data
    except Exception as e:
        print(f"[IntentParser] Error parsing intent: {e}")
        return {
            "type": "question",
            "goal": None,
            "command": None,
            "response": "I'm having trouble understanding right now. Could you rephrase that?",
            "confidence": 0.0
        }
