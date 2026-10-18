# For hosts that run containers (Koyeb, Render, Railway, Fly.io, your own VPS...).
# Set the variables from .env.example as environment variables on the host
# instead of shipping a .env file.
FROM python:3.12-slim
WORKDIR /app
COPY bot.py .
CMD ["python", "-u", "bot.py"]
