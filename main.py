from fastapi import FastAPI, HTTPException
from bs4 import BeautifulSoup
import httpx
from datetime import datetime, timedelta
import pytz
from typing import List, Dict, Optional

app = FastAPI()

async def fetch_forex_factory_data() -> List[Dict]:
    url = "https://www.forexfactory.com"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        if response.status_code != 200:
            raise HTTPException(status_code=500, detail="Failed to fetch Forex Factory data")
        
        soup = BeautifulSoup(response.text, 'html.parser')
        calendar_table = soup.find('table', class_='calendar__table')
        
        if not calendar_table:
            raise HTTPException(status_code=500, detail="Failed to find calendar table")
        
        events = []
        current_date = None
        
        for row in calendar_table.find_all('tr', class_=['calendar__row', 'calendar_row']):
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
        
        return events

@app.get("/calendar")
async def get_economic_calendar():
    try:
        events = await fetch_forex_factory_data()
        
        # Format the response
        formatted_events = []
        for event in events:
            formatted_event = (
                f"🕒 {event['time']}\n"
                f"💱 {event['currency']}\n"
                f"📊 Impact: {event['impact']}\n"
                f"📰 {event['event']}\n"
                f"───────────────"
            )
            formatted_events.append(formatted_event)
        
        return {
            "status": "success",
            "events": formatted_events
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
