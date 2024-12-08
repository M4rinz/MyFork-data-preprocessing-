import json

from fastapi import FastAPI, Query, HTTPException
from datetime import datetime
from typing import Optional
from src.app.on_request_pipeline import get_request
from src.app.real_time.publisher import KafkaPublisher
from src.app.real_time.request import KPIStreamingRequest, KPIValidator
from src.app.real_time.message import RealTimeKPI
import os
from dotenv import load_dotenv
from src.app.connections_functions import get_next_datapoint
from src.app.dataprocessing_functions import cleaning_pipeline
import uvicorn
import asyncio
from threading import Event
import warnings

warnings.filterwarnings("ignore")

load_dotenv()

KAFKA_TOPIC_NAME = os.getenv("KAFKA_TOPIC_NAME")
KAFKA_SERVER = os.getenv("KAFKA_SERVER")
KAFKA_PORT = os.getenv("KAFKA_PORT")
BATCH_SIZE = 1000

app = FastAPI()
publisher = KafkaPublisher(
    topic=KAFKA_TOPIC_NAME,
    port=KAFKA_PORT,
    servers=KAFKA_SERVER,
)

stop_event = Event()
background_task: Optional[asyncio.Task] = None


@app.on_event("startup")
async def startup_event():
    await publisher.aioproducer.start()


def start():
    print("Starting the data preprocessing API...")
    uvicorn.run("src.app.main:app", host="0.0.0.0", port=8003, reload=True)


@app.on_event("shutdown")
async def shutdown_event():
    await real_time_streaming_stop()
    if publisher.aioproducer:
        await publisher.aioproducer.stop()


@app.get("/")
def root():
    return {"message": "Data preprocessing in progress."}


@app.get("/get_request")
def get_request_callback(
    machine_name: str = Query(..., description="Name of the machine"),
    asset_id: str = Query(..., description="ID of the asset"),
    kpi: str = Query(..., description="Key Performance Indicator"),
    operation: str = Query(..., description="Type of operation"),
    timestamp_start: datetime = Query(..., description="Start timestamp in ISO format"),
    timestamp_end: datetime = Query(..., description="End timestamp in ISO format"),
    transformation: Optional[str] = Query(
        None, description="Transformation type (e.g., 'S', 'T')"
    ),
    forecasting: bool = Query(False, description="Enable forecasting (true/false)"),
):
    try:
        json_transformed_data = get_request(
            machine_name,
            asset_id,
            kpi,
            operation,
            timestamp_start,
            timestamp_end,
            transformation,
            forecasting,
        )
        return json_transformed_data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/real-time/start")
async def real_time_streaming_start(kpi_streaming_request: KPIStreamingRequest):
    global background_task, stop_event
    if background_task and not background_task.done():
        return {"message": "Task is already running!"}

    stop_event.clear()
    await asyncio.sleep(1)
    # Get the current running event loop and create the task inside it
    background_task = asyncio.create_task(send_kpis(kpi_streaming_request))

    return {"message": "Background task started!"}


async def send_kpis(kpi_streaming_request: KPIStreamingRequest):
    global stop_event
    i = 0

    try:
        kpi_validator = KPIValidator.from_streaming_request(kpi_streaming_request)
    except Exception as e:
        print(f"Error initializing KPIValidator: {e}")
        return

    accumulated_data = {kpi: ("", []) for kpi in kpi_validator.kpis}

    iterator = get_next_datapoint()

    while not stop_event.is_set():
        try:
            # Fetch and process the data point
            raw_data = next(iterator)
            cleaned_data = cleaning_pipeline(raw_data)

            if cleaned_data is None:
                print(f"Data point {i} could not be fetched. Skipping...")
                continue

            kpi_name = cleaned_data["kpi"]
            if kpi_validator.validate(cleaned_data):
                aggregation_column = kpi_validator.get_aggregation_from_kpi(kpi_name)
                accumulated_data[kpi_name] = (
                    aggregation_column,
                    accumulated_data[kpi_name][1] + [cleaned_data[aggregation_column]],
                )

            # Check readiness of all KPIs
            if all(
                len(data[1]) == kpi_validator.machine_count
                for data in accumulated_data.values()
            ):
                message = [
                    RealTimeKPI(kpi, column, values).to_json()
                    for kpi, (column, values) in accumulated_data.items()
                ]
                for kpi in accumulated_data.keys():
                    accumulated_data[kpi] = ("", [])
                await publisher.aioproducer.send_and_wait(
                    publisher._topic, json.dumps(message).encode("utf-8")
                )

        except Exception as e:
            print(f"Error in loop: {e}")
            stop_event.set()
            break

        i = (i + 1) % BATCH_SIZE
        # await asyncio.sleep(0.1)

    print("Exiting KPI streaming loop.")


@app.get("/real-time/stop")
async def real_time_streaming_stop():
    """Stop the background task."""
    global stop_event, background_task

    if not background_task or background_task.done():
        return {"message": "No KPI streaming in progress to stop."}

    print("Stopping the real-time streaming...")
    stop_event.set()  # Signal to stop the task
    await background_task  # Wait for the task to finish

    return {"message": "KPI Streaming stopped!"}


@app.get("/health/")
def health_check():
    return {"status": "ok"}