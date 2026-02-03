#!/usr/bin/env python3

import logging
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from src.config import config
from src.services.pollinations import pollinations_client
from src.logging_config import setup_logging

setup_logging(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Polly API", description="OpenAI-compatible API for Polly bot")

class Message(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[Message]
    system: Optional[str] = "You are Polly, a helpful assistant integrated with GitHub and code development tools. You have access to tools for creating and managing GitHub issues, searching code, running tasks, web search, and more."
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None

class ChatResponse(BaseModel):
    content: str
    stop_reason: str

@app.on_event("startup")
async def startup_event():
    logger.info("Polly API starting...")
    config.validate()
    
    for name, handler in get_tool_handlers().items():
        pollinations_client.register_tool_handler(name, handler)
    logger.info(f"Registered {len(get_tool_handlers())} tool handlers")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("Polly API shutting down...")
    await pollinations_client.close()

def get_tool_handlers():
    from src.services.github import TOOL_HANDLERS
    from src.services.code_agent.tools import TOOL_HANDLERS as CODE_AGENT_HANDLERS
    
    handlers = dict(TOOL_HANDLERS)
    handlers.update(CODE_AGENT_HANDLERS)
    
    if config.local_embeddings_enabled:
        from src.bot import _code_search_handler
        handlers["code_search"] = _code_search_handler
    
    from src.services.pollinations import web_search_handler, web_handler
    handlers["web_search"] = web_search_handler
    handlers["web"] = web_handler
    
    from src.services.web_scraper import web_scrape_handler
    handlers["web_scrape"] = web_scrape_handler
    
    from src.services.discord_search import tool_discord_search
    handlers["discord_search"] = tool_discord_search
    
    return handlers

@app.post("/v1/chat/completions", response_model=ChatResponse)
async def chat_completions(request: ChatRequest) -> ChatResponse:
    messages = [{"role": m.role, "content": m.content} for m in request.messages]
    
    try:
        response = await pollinations_client.process_with_tools(
            messages=messages,
            system_prompt=request.system,
        )
        
        return ChatResponse(
            content=response,
            stop_reason="end_turn"
        )
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "bot_name": config.bot_name,
    }

if __name__ == "__main__":
    logger.info("Starting Polly API...")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")
