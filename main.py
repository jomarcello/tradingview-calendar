from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import httpx
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Optional
import os
from openai import OpenAI
import logging
from logging.handlers import RotatingFileHandler
import traceback
import asyncio
import json
import feedparser
import re

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Log to console
        RotatingFileHandler(
            '/tmp/economic_calendar.log',  # Use /tmp for Railway
            maxBytes=10485760,  # 10MB
            backupCount=5
        )
    ]
)
logger = logging.getLogger(__name__)

app = FastAPI()

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global error handler caught: {str(exc)}")
    logger.error(f"Traceback: {traceback.format_exc()}")
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "type": type(exc).__name__}
    )

# Initialize OpenAI client only if API key is available
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
logger.info(f"OPENAI_API_KEY present: {OPENAI_API_KEY is not None}")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
logger.info(f"OpenAI client initialized: {client is not None}")

def extract_currency(title: str) -> str:
    """Extract currency from event title."""
    currencies = ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]
    for currency in currencies:
        if currency in title:
            return currency
    return "OTHER"

def determine_impact(title: str, description: str) -> str:
    """Determine impact level based on keywords."""
    high_impact = ["NFP", "CPI", "GDP", "PMI", "Rate Decision", "Employment"]
    medium_impact = ["Retail Sales", "Trade Balance", "Manufacturing", "Consumer"]
    
    title_upper = title.upper()
    desc_upper = description.upper()
    
    for term in high_impact:
        if term.upper() in title_upper or term.upper() in desc_upper:
            return "🔴"
    for term in medium_impact:
        if term.upper() in title_upper or term.upper() in desc_upper:
            return "🟡"
    return "🟢"

async def format_with_ai(events: List[Dict]) -> str:
    try:
        if not client:
            logger.warning("No OpenAI client available, using fallback formatting")
            # Fallback formatting if no OpenAI key is available
            return "\n".join([
                f"🕒 {event['time']} | {event['currency']} | {event['impact']}\n"
                f"📊 {event['event']}\n"
                f"📈 Actual: {event.get('actual', 'N/A')} | Forecast: {event.get('forecast', 'N/A')}\n"
                for event in events
            ])

        logger.info("Formatting events with OpenAI")
        # Create a prompt for GPT
        events_text = "\n".join([
            f"Time: {event['time']}, Currency: {event['currency']}, "
            f"Impact: {event['impact']}, Event: {event['event']}, "
            f"Actual: {event.get('actual', 'N/A')}, Forecast: {event.get('forecast', 'N/A')}"
            for event in events
        ])
        
        prompt = f"""Here are today's economic events:
{events_text}

Format this into a clear, concise summary. Focus on high-impact events.
Group by currency. Keep it brief but informative.
Use emojis for better readability.
Include actual and forecast values if available.
Format times in 24-hour format."""

        logger.info("Sending request to OpenAI")
        response = await client.chat.completions.create(
            model="gpt-4-1106-preview",
            messages=[
                {"role": "system", "content": "You are a forex economic calendar assistant. Be concise and clear."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=500,
            temperature=0.7
        )
        logger.info("Received response from OpenAI")
        
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Error in format_with_ai: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        # Fallback formatting if OpenAI call fails
        return "\n".join([
            f"🕒 {event['time']} | {event['currency']} | {event['impact']}\n"
            f"📊 {event['event']}\n"
            f"📈 Actual: {event.get('actual', 'N/A')} | Forecast: {event.get('forecast', 'N/A')}\n"
            for event in events
        ])

async def fetch_economic_calendar_data() -> List[Dict]:
    try:
        # Get current date in UTC
        now = datetime.now(pytz.UTC)
        today = now.strftime("%Y-%m-%d")
        
        # Investing.com Economic Calendar RSS feed
        url = "https://www.investing.com/rss/economic_calendar.rss"
        logger.info(f"Fetching data from {url}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            # Parse RSS feed
            feed = feedparser.parse(response.text)
            logger.info(f"Received {len(feed.entries)} entries from RSS feed")
            
            events = []
            for entry in feed.entries:
                try:
                    # Parse event time
                    event_time = datetime.strptime(entry.published, "%a, %d %b %Y %H:%M:%S %z")
                    
                    # Only include events from today
                    if event_time.strftime("%Y-%m-%d") != today:
                        continue
                    
                    # Extract event details
                    currency = extract_currency(entry.title)
                    impact = determine_impact(entry.title, entry.description)
                    
                    # Try to extract actual and forecast values from description
                    actual_match = re.search(r"Actual: ([^,]+)", entry.description)
                    forecast_match = re.search(r"Forecast: ([^,]+)", entry.description)
                    
                    events.append({
                        "time": event_time.strftime("%H:%M"),
                        "currency": currency,
                        "impact": impact,
                        "event": entry.title,
                        "actual": actual_match.group(1) if actual_match else None,
                        "forecast": forecast_match.group(1) if forecast_match else None
                    })
                    
                except Exception as e:
                    logger.error(f"Error processing entry: {str(e)}")
                    logger.error(f"Entry data: {entry}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue
            
            # Sort events by time
            events.sort(key=lambda x: x['time'])
            
            logger.info(f"Processed {len(events)} events for {today}")
            return events
            
    except Exception as e:
        logger.error(f"Error fetching data: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return []

@app.get("/calendar")
async def get_economic_calendar():
    try:
        logger.info("Getting economic calendar")
        events = await fetch_economic_calendar_data()
        
        if not events:
            return {
                "status": "success",
                "events": ["No economic events found for today."]
            }
        
        formatted_text = await format_with_ai(events)
        
        return {
            "status": "success",
            "events": [formatted_text]
        }
    except Exception as e:
        logger.error(f"Error in get_economic_calendar: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
