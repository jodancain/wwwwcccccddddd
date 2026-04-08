import uvicorn
from app.config.settings import get_settings


def main():
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=True,
    )


if __name__ == "__main__":
    main()
