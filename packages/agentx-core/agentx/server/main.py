import uvicorn
import asyncio
from fastapi import FastAPI
from agentx.server.api import app
from agentx.server.loop import agentx_loop

@app.on_event("startup")
async def startup_event():
    # Start the background AgentX loop when the FastAPI server starts
    asyncio.create_task(agentx_loop())

def main():
    print("Starting AgentX API Server...")
    uvicorn.run("agentx.server.main:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()
