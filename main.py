from fastapi import FastAPI
import logging

# Basic logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI()

@app.get("/")
async def root():
    """Root endpoint to check if service is running"""
    logger.info("Root endpoint called")
    return {"status": "success", "message": "Economic Calendar Service is running"}

@app.get("/test")
async def test():
    """Test endpoint"""
    return {"status": "success", "message": "Test endpoint working"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="debug")
