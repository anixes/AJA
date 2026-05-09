import uvicorn
import asyncio
from fastapi import FastAPI
from agentx.server.api import app
from agentx.server.loop import jarvis_loop

@app.on_event("startup")
async def startup_event():
    # Start the background jarvis loop when the FastAPI server starts
    asyncio.create_task(jarvis_loop())

def main():
    print("Starting AgentX Jarvis Server...")
    uvicorn.run("agentx.server.main:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()
