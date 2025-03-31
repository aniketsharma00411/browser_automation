from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional, List, Dict
import uvicorn

from src.browser_agent import BrowserAgent

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="Browser Automation AI Agent")


class Command(BaseModel):
    command: str


class ExtractionRequest(BaseModel):
    query: str  # Natural language query for extraction


class BrowserConfig(BaseModel):
    proxy_config: Optional[Dict[str, str]] = None
    extensions: Optional[List[str]] = None


# Initialize browser agent with default configuration
browser_agent = BrowserAgent()


@app.on_event("startup")
async def startup_event():
    global browser_agent
    await browser_agent.start()


@app.on_event("shutdown")
async def shutdown_event():
    global browser_agent
    await browser_agent.stop()


@app.post("/api/configure")
async def configure_browser(config: BrowserConfig):
    """Configure browser with proxy settings and extensions"""
    try:
        global browser_agent
        # Stop the current browser instance
        await browser_agent.stop()

        # Create a new browser agent with the new configuration
        browser_agent = BrowserAgent(
            proxy_config=config.proxy_config,
            extensions=config.extensions
        )

        # Start the browser with new configuration
        await browser_agent.start()

        return {"status": "success", "message": "Browser configured successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/interact")
async def interact(command: Command):
    try:
        global browser_agent
        result = await browser_agent.execute_command(command.command)
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["message"])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/extract")
async def extract(request: ExtractionRequest):
    """Extract data from the current page based on natural language query"""
    try:
        global browser_agent
        result = await browser_agent.extract(request.query)
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["message"])
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
