import typer
import json

async def plan_gate(goal_request: str) -> str:
    """
    Evaluates a goal request. If it warrants an execution plan, generates one,
    prints it, and prompts the user for approval.
    
    Returns the user's updated instruction or the original goal if approved/no plan needed.
    """
    from rich.prompt import Prompt
    from aja.interface.modern import console
    from aja.orchestration.gateway import LLMGateway
    from aja.config import AJA_PLANNER_MODEL
    
    gateway = LLMGateway()
    model = AJA_PLANNER_MODEL or "google:gemini-2.0-flash"
    
    # 1. Judgement
    judgement_prompt = f"""You are AJA, an expert AI assistant.
Analyze the following user goal: 
<user_goal>
{goal_request}
</user_goal>

Does this goal require a detailed execution plan before acting? 
Answer YES if it requires architectural changes, significant file creation, multi-step coordination, or is vague. 
Answer NO if it's a simple lookup, script run, one-file minor edit, or straightforward isolated task.
Respond ONLY with a JSON object: {{"needs_plan": true/false}}
"""
    
    console.print("[dim]Analyzing request complexity...[/]")
    try:
        response = await gateway.chat(
            model=model,
            prompt=[{"role": "user", "content": judgement_prompt}],
            system="You are a JSON-only evaluation bot."
        )
        
        resp_text = response if isinstance(response, str) else response.get("content", "")
        start_idx = resp_text.find('{')
        end_idx = resp_text.rfind('}')
        if start_idx != -1 and end_idx != -1:
            data = json.loads(resp_text[start_idx:end_idx+1])
            needs_plan = data.get("needs_plan", True)
        else:
            needs_plan = True # Default safe
    except Exception as e:
        console.print(f"[dim]Complexity check skipped ({e}). Proceeding directly.[/dim]")
        return goal_request
        
    if not needs_plan:
        return goal_request
        
    # 2. Plan Generation
    console.print("[bold yellow]Generating Execution Plan...[/]")
    plan_prompt = f"""You are AJA, an expert AI assistant.
The user wants to achieve this goal:
<user_goal>
{goal_request}
</user_goal>

Write a brief, structured execution plan outlining what you will do. Keep it concise.
Include:
- Objective
- Files to modify/create
- High-level steps
"""
    try:
        plan_resp = await gateway.chat(
            model=model,
            prompt=[{"role": "user", "content": plan_prompt}],
            system="You are an expert software architect providing concise execution plans."
        )
        plan = plan_resp if isinstance(plan_resp, str) else plan_resp.get("content", "Error generating plan.")
    except Exception as e:
        plan = f"Failed to generate plan: {e}"
        
    console.print("\n[bold cyan]=== Proposed Execution Plan ===[/]")
    console.print(plan)
    console.print("[bold cyan]===============================[/]\n")
    
    response = Prompt.ask("[bold green]Proceed with plan? [Y/n/adjust][/]", default="Y")
    
    if response.lower() in ('y', 'yes', ''):
        return f"{goal_request}\n\nExecution Plan to follow:\n{plan}"
    elif response.lower() in ('n', 'no'):
        console.print("[red]Task aborted.[/]")
        raise typer.Exit()
    else:
        return f"{goal_request}\n\nExecution Plan to follow:\n{plan}\n\nUser Adjustments:\n{response}"
