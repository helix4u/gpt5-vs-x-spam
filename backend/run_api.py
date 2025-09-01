import sys
import uvicorn


def main():
    # No reload for simplicity/stability on Windows
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
