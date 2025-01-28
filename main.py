import logging
import os
from datetime import datetime, timedelta
import pytz
import traceback
import asyncio
import json
import time
import re
from typing import List, Dict
import httpx

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Sample economic events database
ECONOMIC_EVENTS = {
    "2025-01-28": [
        {
            "time": "08:30",
            "currency": "GBP",
            "impact": "",
            "event": "GDP Growth Rate QoQ",
            "actual": None,
            "forecast": "0.2%"
        },
        {
            "time": "09:00",
            "currency": "EUR",
            "impact": "",
            "event": "Industrial Production MoM",
            "actual": None,
            "forecast": "0.3%"
        },
        {
            "time": "13:30",
            "currency": "USD",
            "impact": "",
            "event": "Core PCE Price Index MoM",
            "actual": None,
            "forecast": "0.2%"
        }
    ],
    "2025-01-29": [
        {
            "time": "10:00",
            "currency": "EUR",
            "impact": "",
            "event": "ECB Interest Rate Decision",
            "actual": None,
            "forecast": "4.5%"
        },
        {
            "time": "13:30",
            "currency": "USD",
            "impact": "",
            "event": "Initial Jobless Claims",
            "actual": None,
            "forecast": "205K"
        }
    ],
    "2025-01-30": [
        {
            "time": "00:30",
            "currency": "AUD",
            "impact": "",
            "event": "CPI QoQ",
            "actual": None,
            "forecast": "0.8%"
        },
        {
            "time": "13:30",
            "currency": "CAD",
            "impact": "",
            "event": "GDP MoM",
            "actual": None,
            "forecast": "0.2%"
        },
        {
            "time": "19:00",
            "currency": "USD",
            "impact": "",
            "event": "Fed Interest Rate Decision",
            "actual": None,
            "forecast": "5.5%"
        }
    ]
}

def determine_impact(event: Dict) -> str:
    """Determine impact level based on importance and event name."""
    # Check event name for high-impact keywords
    high_impact = ["NFP", "CPI", "GDP", "PMI", "Rate", "Employment", "Interest"]
    medium_impact = ["Retail", "Trade", "Manufacturing", "Consumer", "Production"]
    
    event_name = event.get('Event', '').upper()
    
    for term in high_impact:
        if term.upper() in event_name:
            return ''
    for term in medium_impact:
        if term.upper() in event_name:
            return ''
    return ''

async def fetch_economic_calendar_data() -> List[Dict]:
    try:
        # Get current date in UTC
        now = datetime.now(pytz.UTC)
        today = now.strftime("%Y-%m-%d")
        
        # Get events for today from our database
        events = ECONOMIC_EVENTS.get(today, [])
        
        # Filter out past events
        current_time = now.strftime("%H:%M")
        upcoming_events = [
            event for event in events
            if event['time'] > current_time
        ]
        
        # Sort events by time
        upcoming_events.sort(key=lambda x: x['time'])
        
        # Determine impact for each event
        for event in upcoming_events:
            event['impact'] = determine_impact(event)
        
        logger.info(f"Found {len(upcoming_events)} upcoming events for {today}")
        return upcoming_events
            
    except Exception as e:
        logger.error(f"Error fetching calendar data: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return []

async def send_to_telegram(events: List[str]):
    """Send events to Telegram service"""
    try:
        telegram_url = "https://tradingview-telegram-service-production.up.railway.app/api/send_message"
        message = "\n".join(events)
        
        logger.info(f"Sending to Telegram: {message}")
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            logger.info(f"Making request to {telegram_url}")
            response = await client.post(
                telegram_url,
                json={
                    "message": message,
                    "parse_mode": "HTML",
                    "chat_id": "-1002047725461"
                }
            )
            logger.info(f"Response status: {response.status_code}")
            logger.info(f"Response text: {response.text}")
            
            response.raise_for_status()
            logger.info("Successfully sent events to Telegram")
            return {"status": "success"}
            
    except Exception as e:
        error_msg = f"Error sending to Telegram: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {"status": "error", "detail": error_msg}

@app.get("/calendar")
async def get_calendar():
    try:
        logger.info("Starting calendar request")
        events = await fetch_economic_calendar_data()
        logger.info(f"Fetched events: {events}")
        
        if not events:
            message = ["No economic events found for today."]
            logger.info(f"No events found, sending message: {message}")
            result = await send_to_telegram(message)
            if result.get("status") == "error":
                return result
            return {"status": "success", "events": message}
            
        # Format events for display
        formatted_events = []
        events_by_currency = {}
        
        # Group events by currency
        for event in events:
            currency = event['currency']
            if currency not in events_by_currency:
                events_by_currency[currency] = []
            events_by_currency[currency].append(event)
        
        logger.info(f"Grouped events by currency: {events_by_currency}")
        
        # Format each currency group
        for currency, currency_events in events_by_currency.items():
            currency_header = f"\n<b>{currency} Events:</b>"
            formatted_events.append(currency_header)
            
            # Format each event
            for event in currency_events:
                event_time = event['time']
                impact = event['impact']
                event_name = event['event']
                forecast = f"(Forecast: {event['forecast']})" if event['forecast'] else ""
                actual = f"(Actual: {event['actual']})" if event['actual'] else ""
                
                formatted_event = f"{event_time} {impact} {event_name} {forecast} {actual}"
                formatted_events.append(formatted_event)
        
        # Log the formatted events
        logger.info(f"Formatted events: {formatted_events}")
        
        # Send to Telegram
        result = await send_to_telegram(formatted_events)
        if result.get("status") == "error":
            return result
            
        return {"status": "success", "events": formatted_events}
        
    except Exception as e:
        error_msg = f"Error in get_calendar: {str(e)}"
        logger.error(error_msg)
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {"status": "error", "detail": error_msg}

@app.get("/")
async def root():
    return {"status": "success", "message": "Economic Calendar Service is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
