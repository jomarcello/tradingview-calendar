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
import time
import re
import feedparser

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

async def format_with_ai(events: List[Dict]) -> str:
    """Format events without using OpenAI."""
    # Group events by currency
    events_by_currency = {}
    for event in events:
        currency = event['currency']
        if currency not in events_by_currency:
            events_by_currency[currency] = []
        events_by_currency[currency].append(event)
    
    # Format each currency group
    formatted_parts = []
    for currency, currency_events in sorted(events_by_currency.items()):
        # Add currency header
        formatted_parts.append(f"*{currency} Events*")
        
        # Sort events by time
        currency_events.sort(key=lambda x: x['time'])
        
        # Format each event
        for event in currency_events:
            time = event['time']
            impact = event['impact']
            event_name = event['event']
            actual = event.get('actual', 'N/A')
            forecast = event.get('forecast', 'N/A')
            
            formatted_parts.append(
                f"🕒 {time} {impact}\n"
                f"{event_name}\n"
                f"Forecast: {forecast}"
            )
        
        formatted_parts.append("")  # Add blank line between currency groups
    
    return "\n".join(formatted_parts)

def determine_impact(event: Dict) -> str:
    """Determine impact level based on importance and event name."""
    # Check event name for high-impact keywords
    high_impact = ["NFP", "CPI", "GDP", "PMI", "Rate", "Employment", "Interest"]
    medium_impact = ["Retail", "Trade", "Manufacturing", "Consumer", "Production"]
    
    event_name = event.get('Event', '').upper()
    
    for term in high_impact:
        if term.upper() in event_name:
            return '🔴'
    for term in medium_impact:
        if term.upper() in event_name:
            return '🟡'
    return '🟢'

async def fetch_economic_calendar_data() -> List[Dict]:
    try:
        # Get current date in UTC
        now = datetime.now(pytz.UTC)
        today = now.strftime("%Y-%m-%d")
        
        # Investing.com Economic Calendar RSS Feed
        url = "https://www.investing.com/rss/economic_calendar.rss"
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.114 Safari/537.36"
        }
        
        # Get data from Investing.com
        logger.info(f"Fetching calendar data from Investing.com for {today}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers, follow_redirects=True)
            response.raise_for_status()
            
            # Parse RSS feed
            feed = feedparser.parse(response.text)
            
            logger.info(f"Received {len(feed.entries)} events from Investing.com")
            
            # Convert to our format
            events = []
            for entry in feed.entries:
                try:
                    # Parse the title to extract information
                    # Example: "(EU) ECB President Lagarde Speech"
                    title = entry.title
                    
                    # Extract currency from title (in parentheses)
                    currency_match = re.search(r'\((.*?)\)', title)
                    if not currency_match:
                        continue
                        
                    currency = currency_match.group(1)
                    event_name = title.split(') ', 1)[1] if ') ' in title else title
                    
                    # Convert currency codes
                    currency_map = {
                        "EU": "EUR",
                        "UK": "GBP",
                        "US": "USD",
                        "JP": "JPY",
                        "AU": "AUD",
                        "CA": "CAD",
                        "CH": "CHF",
                        "NZ": "NZD"
                    }
                    
                    # Determine impact based on keywords
                    high_impact = ["GDP", "CPI", "NFP", "PMI", "Rate Decision", "Employment"]
                    medium_impact = ["Trade Balance", "Retail Sales", "Industrial Production"]
                    
                    impact = "🔴" if any(kw in event_name for kw in high_impact) else \
                            "🟡" if any(kw in event_name for kw in medium_impact) else "🟢"
                    
                    # Parse time from published date
                    event_time = datetime.fromtimestamp(time.mktime(entry.published_parsed), pytz.UTC)
                    
                    # Format the event
                    formatted_event = {
                        "time": event_time.strftime("%H:%M"),
                        "currency": currency_map.get(currency, currency),
                        "impact": impact,
                        "event": event_name,
                        "actual": None,  # RSS feed doesn't include these
                        "forecast": None
                    }
                    
                    # Only add events that haven't happened yet and are for today
                    if event_time > now and event_time.date() == now.date():
                        events.append(formatted_event)
                        logger.info(f"Added event: {formatted_event['event']} at {formatted_event['time']}")
                    
                except Exception as e:
                    logger.error(f"Error processing event: {str(e)}")
                    logger.error(f"Event data: {entry}")
                    continue
            
            # Sort events by time
            events.sort(key=lambda x: x['time'])
            
            logger.info(f"Processed {len(events)} upcoming events for {today}")
            return events
            
    except Exception as e:
        logger.error(f"Error fetching calendar data: {str(e)}")
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
