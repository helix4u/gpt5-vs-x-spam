import sys
import os
import uvicorn
from app.config import settings


def main():
    # No reload for simplicity/stability on Windows
    host = settings.api_host
    port = settings.api_port
    uvicorn.run("app.api:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
