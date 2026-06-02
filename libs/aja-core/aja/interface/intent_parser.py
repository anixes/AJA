import json
from typing import Dict, List, Any
import aja.config
from aja.llm import get_gateway_for_model

def parse_intent(message: str, history: List[Dict[str, Any]], system_state: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Convert natural language -> structured action.
    """
    model_name = aja.config.AJA_PLANNER_MODEL
    # gw is not needed here
    
    system_prompt = """You are AJA (Assistant of Joint Agents), a highly capable AI assistant, personal secretary, and operator for AJA Core.
Analyze the user's message and the conversation history.
Determine if the user wants to:
1. "terminal": For local OS operations and PC control. You are an expert system administrator empowered to control the OS natively. Use advanced tools (e.g., on Windows use `powershell -c "..."` to chain cmdlets like Get-Process). IMPORTANT: On Windows, ALWAYS wrap paths in double quotes (e.g., dir "D:\\path").
2. "goal": For complex tasks requiring multiple steps, coding, or reasoning.
3. "question": Ask a general question or chat.
4. "control": Manage system state, run diagnostics, read logs, or change settings.

For "control", map specific system requests to these commands:
- "status": When user asks about current system state, general status, or overview of the agent network.
- "doctor": When user asks for diagnostics or system readiness checks.
- "gpu": When user asks about GPU status, hardware resources, hardware utilization, or memory/CPU/RAM diagnostics.
- "logs": When user asks to show, view, read, check, or tail the system/agent logs.
- "pause": Pause the current mission/run.
- "resume": Resume the current mission/run.
- "exit": Quit the session.

Respond ONLY in valid JSON format:
{
    "type": "terminal" | "goal" | "question" | "control",
    "goal": "Extracted goal description if type is 'goal', else null",
    "command": "Exact shell command if type is 'terminal', OR status/doctor/gpu/logs/pause/resume/exit if type is 'control', else null",
    "response": "Conversational response to the user.",
    "confidence": 0.0 to 1.0
}

CRITICAL CLASSIFICATION RULES:
- The 'response' string MUST reflect your premium assistant and secretary persona. Be polite, refined, deeply helpful, and loyal (using terms like 'Sir', 'My friend', 'Operator', or 'Indeed' when appropriate), yet remain casual, highly developer-fluent, concise, and possess a sharp conversational intelligence. Never sound robotic or overly corporate.
- If the request is ambiguous (e.g. 'deploy it'), ask a follow-up question via the 'response' field as a helpful secretary seeking clarification, and set type to 'question'.
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
        from aja.llm import completion
        raw = completion(prompt=prompt, system_prompt=system_prompt, model=model_name)
        if not raw:
            raise ValueError("No response from LLM gateway")
            
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
