from fastapi import FastAPI, HTTPException
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional, List, Dict
import os
import uvicorn
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from src import chat_interface
from src.browser_agent import BrowserAgent

# Load environment variables
load_dotenv()

# Initialize FastAPI app
app = FastAPI(title="Browser Automation AI Agent")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize browser agent with default configuration
browser_agent = BrowserAgent()

# Inject browser agent into chat interface
chat_interface.set_browser_agent(browser_agent)

# Include the chat interface router
app.include_router(chat_interface.router, prefix="")

# Get the absolute path to the static directory
static_dir = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "src", "static")

# Mount static files
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class Command(BaseModel):
    command: str


class ExtractionRequest(BaseModel):
    query: str  # Natural language query for extraction


class BrowserConfig(BaseModel):
    proxy_config: Optional[Dict[str, str]] = None
    extensions: Optional[List[str]] = None


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


@app.get("/chat/new")
async def create_new_chat():
    """Create a new chat and redirect to it"""
    try:
        print("Creating new chat")
        chat_id = await chat_interface.chat_manager.create_chat()
        return RedirectResponse(url=f"/chat/{chat_id}")
    except Exception as e:
        return RedirectResponse(url="/")


@app.get("/chat/{chat_id}")
async def get_chat_page(chat_id: str):
    """Serve the chat interface for a specific chat ID"""
    try:
        # First validate if chat exists
        exists = await chat_interface.chat_manager.validate_chat_id(chat_id)
        if not exists:
            # If chat doesn't exist, redirect to new chat
            return RedirectResponse(url="/chat/new")

        # If chat exists, serve the page
        return FileResponse(os.path.join(static_dir, "index.html"))
    except Exception as e:
        # If there's any error, redirect to new chat
        return RedirectResponse(url="/chat/new")


@app.get("/")
async def get_root():
    """Redirect root to a new chat"""
    return RedirectResponse(url="/chat/new")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
