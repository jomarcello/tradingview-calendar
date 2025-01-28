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
        
        # TradingView Economic Calendar API
        url = "https://scanner.tradingview.com/economics/scan"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        
        # Request body for today's events
        body = {
            "filter": [
                {"left": "date", "operation": "in_range", "right": [today, today]},
                {"left": "country", "operation": "in_range", "right": ["US", "EU", "GB", "JP", "AU", "CA", "CH", "NZ"]}
            ]
        }
        
        # Get data from TradingView
        logger.info(f"Fetching calendar data from TradingView for {today}")
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            
            logger.info(f"Received {len(data.get('data', []))} events from TradingView")
            
            # Convert to our format
            events = []
            for event in data.get('data', []):
                try:
                    # Extract time in UTC
                    event_time = datetime.fromtimestamp(event['d'][0], pytz.UTC)
                    
                    # Map country to currency
                    country_to_currency = {
                        "US": "USD", "EU": "EUR", "GB": "GBP", "JP": "JPY",
                        "AU": "AUD", "CA": "CAD", "CH": "CHF", "NZ": "NZD"
                    }
                    
                    # Determine impact based on importance
                    importance = event['d'][3]  # 0-100 scale
                    if importance >= 70:
                        impact = "🔴"
                    elif importance >= 40:
                        impact = "🟡"
                    else:
                        impact = "🟢"
                    
                    # Format the event
                    formatted_event = {
                        "time": event_time.strftime("%H:%M"),
                        "currency": country_to_currency.get(event['d'][1], 'OTHER'),
                        "impact": impact,
                        "event": event['d'][2],  # Event name
                        "actual": event['d'][4] if len(event['d']) > 4 else None,
                        "forecast": event['d'][5] if len(event['d']) > 5 else None
                    }
                    
                    # Only add events that haven't happened yet
                    if event_time > now:
                        events.append(formatted_event)
                        logger.info(f"Added event: {formatted_event['event']} at {formatted_event['time']}")
                    
                except Exception as e:
                    logger.error(f"Error processing event: {str(e)}")
                    logger.error(f"Event data: {event}")
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
