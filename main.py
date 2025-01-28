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
                f"🕒 {event['time']} | {event['currency']} | {event['impact']}\n{event['event']}\n"
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
    
    url = f"https://www.fxstreet.com/economic-calendar/calendar?date={date_str}"
    logger.info(f"Fetching data from {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    }
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        try:
            # Add a small delay to avoid rate limiting
            await asyncio.sleep(1)
            
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            logger.info(f"Got response from {url}, status: {response.status_code}")
            logger.info(f"Response headers: {dict(response.headers)}")
            
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Find all event rows
            events = []
            event_rows = soup.find_all('div', class_='fxs_event_row')
            
            for row in event_rows:
                try:
                    # Get time (in UTC)
                    time_elem = row.find('time')
                    if not time_elem:
                        continue
                    event_time = time_elem.get('datetime', '').split('T')[1][:5]  # Get HH:MM from datetime
                    
                    # Get currency
                    currency_elem = row.find('span', class_='fxs_flag_name')
                    if not currency_elem:
                        continue
                    currency = currency_elem.text.strip()
                    
                    # Get event name
                    event_elem = row.find('h4', class_='fxs_headline')
                    if not event_elem:
                        continue
                    event = event_elem.text.strip()
                    
                    # Get impact
                    impact_elem = row.find('span', class_='fxs_impact_bull')
                    impact = len(impact_elem.find_all('i', class_='fa-circle')) if impact_elem else 0
                    impact_level = '🔴' if impact == 3 else '🟡' if impact == 2 else '🟢' if impact == 1 else '⚪️'
                    
                    # Get actual and forecast values
                    actual = row.find('span', class_='fxs_actualValue')
                    forecast = row.find('span', class_='fxs_forecastValue')
                    
                    events.append({
                        "time": event_time,
                        "currency": currency,
                        "impact": impact_level,
                        "event": event,
                        "actual": actual.text.strip() if actual else None,
                        "forecast": forecast.text.strip() if forecast else None
                    })
                    
                except Exception as e:
                    logger.error(f"Error processing row: {str(e)}")
                    logger.error(f"Row content: {row}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue
            
            logger.info(f"Found {len(events)} events for {date_str}")
            return events
            
        except Exception as e:
            logger.error(f"Error fetching data: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch economic calendar data: {str(e)}")

@app.get("/calendar")
async def get_economic_calendar():
    try:
        logger.info("Getting economic calendar")
        events = await fetch_economic_calendar_data()
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
