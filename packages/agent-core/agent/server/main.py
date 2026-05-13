import uvicorn
import asyncio
from fastapi import FastAPI
from agent.server.api import app
from agent.server.loop import agent_loop

@app.on_event("startup")
async def startup_event():
    # Start the background Agent loop when the FastAPI server starts
    asyncio.create_task(agent_loop())

def main():
    print("Starting Agent API Server...")
    uvicorn.run("agent.server.main:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()
