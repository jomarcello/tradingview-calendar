from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from bs4 import BeautifulSoup
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
    # Get current date in UTC
    now = datetime.now(pytz.UTC)
    date_str = now.strftime("%Y-%m-%d")
    
    # FXStreet API endpoint
    url = "https://api.fxstreet.com/calendar/v1/events/list"
    logger.info(f"Fetching data from {url}")
    
    # Calculate start and end of today in UTC
    start_date = datetime.now(pytz.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    end_date = start_date + timedelta(days=1)
    
    params = {
        "culture": "en-US",
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "volatilities": ["1", "2", "3"],  # Low, Medium, High impact
        "eventCategories": ["Central Banks", "Economic Indicators"],
        "countries": ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"]  # Major currencies
    }
    
    headers = {
        'Accept': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            logger.info(f"Got response from {url}, status: {response.status_code}")
            
            data = response.json()
            logger.info(f"Received {len(data)} events from API")
            
            events = []
            for event in data:
                try:
                    # Convert event time to UTC
                    event_time = datetime.fromisoformat(event['date'].replace('Z', '+00:00'))
                    
                    # Get impact level
                    volatility = event.get('volatility', 0)
                    impact_level = '🔴' if volatility == 3 else '🟡' if volatility == 2 else '🟢' if volatility == 1 else '⚪️'
                    
                    events.append({
                        "time": event_time.strftime("%H:%M"),
                        "currency": event['currency'],
                        "impact": impact_level,
                        "event": event['name'],
                        "actual": event.get('actual'),
                        "forecast": event.get('forecast')
                    })
                except Exception as e:
                    logger.error(f"Error processing event: {str(e)}")
                    logger.error(f"Event data: {event}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue
            
            # Sort events by time
            events.sort(key=lambda x: x['time'])
            
            logger.info(f"Processed {len(events)} events for {date_str}")
            return events
            
        except Exception as e:
            logger.error(f"Error fetching data: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            
            # Fallback to empty list if API fails
            logger.info("Using empty events list as fallback")
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
