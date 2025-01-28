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
            f"Impact: {event['impact']}, Event: {event['event']}"
            for event in events
        ])
        
        prompt = f"""Here are today's economic events:
{events_text}

Format this into a clear, concise summary. Focus on high-impact events.
Group by currency. Keep it brief but informative.
Use emojis for better readability.
Only include time, event name, and impact level."""

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
            f"🕒 {event['time']} | {event['currency']} | {event['impact']}\n{event['event']}\n"
            for event in events
        ])

async def fetch_forex_factory_data() -> List[Dict]:
    url = "https://www.forexfactory.com/calendar"  # Use specific calendar URL
    logger.info(f"Fetching data from {url}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate, br',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1'
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
            calendar_table = soup.find('table', class_='calendar__table')
            
            if not calendar_table:
                logger.error("Failed to find calendar table in response")
                logger.error(f"Response content: {response.text[:1000]}...")  # Log first 1000 chars
                raise HTTPException(status_code=500, detail="Failed to find calendar table")
            
            events = []
            current_date = None
            
            for row in calendar_table.find_all('tr', class_=['calendar__row', 'calendar_row']):
                try:
                    # Check for date row
                    date_cell = row.find('td', class_='calendar__cell--date')
                    if date_cell and date_cell.text.strip():
                        current_date = date_cell.text.strip()
                        continue
                    
                    # Get event details
                    time_cell = row.find('td', class_='calendar__cell--time')
                    currency_cell = row.find('td', class_='calendar__cell--currency')
                    impact_cell = row.find('td', class_='calendar__cell--impact')
                    event_cell = row.find('td', class_='calendar__cell--event')
                    
                    if all([time_cell, currency_cell, impact_cell, event_cell]):
                        time = time_cell.text.strip()
                        currency = currency_cell.text.strip()
                        
                        # Determine impact level
                        impact_spans = impact_cell.find_all('span')
                        impact = len([span for span in impact_spans if 'high' in span.get('class', [])])
                        impact_level = '🔴' if impact == 3 else '🟡' if impact == 2 else '🟢' if impact == 1 else '⚪️'
                        
                        event = event_cell.text.strip()
                        
                        if current_date and time and currency and event:
                            events.append({
                                "date": current_date,
                                "time": time,
                                "currency": currency,
                                "impact": impact_level,
                                "event": event
                            })
                except Exception as e:
                    logger.error(f"Error processing row: {str(e)}")
                    logger.error(f"Row content: {row}")
                    logger.error(f"Traceback: {traceback.format_exc()}")
                    continue
            
            logger.info(f"Found {len(events)} events")
            return events
            
        except Exception as e:
            logger.error(f"Error fetching data: {str(e)}")
            logger.error(f"Traceback: {traceback.format_exc()}")
            raise HTTPException(status_code=500, detail=f"Failed to fetch Forex Factory data: {str(e)}")

@app.get("/calendar")
async def get_economic_calendar():
    try:
        logger.info("Getting economic calendar")
        events = await fetch_forex_factory_data()
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
