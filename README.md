# Dox Scraper
A multi-connection anime scraping pipeline along with telegram client to store the downloaded files.

## Features

- Automated multi-anime scraping
- Fast uploads and downloads by making multiple requests simultaneously
- Reliable providers support
- MongoDB indexing of files

## Upcoming Features

- More providers support
- Anime searching and fetching in Telegram
- Tests

## Requirements

- Python (3.12+)
- Telegram API id, hash
- Telegram bot token
- Telegram channel id
- MongoDB URI

## Setup

### 1. Getting necessary stuff

1. Get your Telegram api id and hash id from [telegram dev](https://my.telegram.org).

2. Use [@BotFather](https://t.me/BotFather) and get your bot token.

3. Create a channel on Telegram and add your bot as admin.

4. Get your **account id** and **channel id** where you added the bot. If you don't know how to get them, use [@raw_data_bot](https://t.me/raw_data_bot). You can write your own script if you don't want to use this bot, but this is the fastest way so far.

5.  
   - Create a [Mongodb free atlas](https://www.mongodb.com/products/platform/atlas-database), or run mongodb server on [Docker](https://www.docker.com) in your machine, and get the URI. For Docker Mongodb setup, you can [follow this tutorial on Youtube](https://youtu.be/7PF-GtZ4C94?t=19).

   - Additionally, you can also use [MongoDB Community Edition](https://www.mongodb.com/try/download/search-in-community) for local setup.

6. Use `.env.example` as your template for environment variables file (`.env`), and fill all the keys.

### 2.1 Running locally (non-Docker)

1. Install [Python 3.13+](https://python.org/downloads), or just install [UV package manager](https://docs.astral.sh/uv/getting-started/installation/) (automatically handles Python version).

2. Create virtual environment and [activate it according to your operating system](https://docs.python.org/3/library/venv.html#how-venvs-work).

   ```sh
   python -m venv # (using pure python)
   uv venv # (using uv)
   ```

3. Install required packages:

   ```sh
   pip install -r requirements.txt # (if using pip)
   uv sync # (if using uv)
   ```

4. Start processes:

   ```sh
   python src/main.py
   ```

### 2.2 Running on Docker

All things have been tested on a Debian linux server and, it must work properly on all machines with ARMv8.2-A (concerns Raspberry Pi) or higher.

1. You don't need to run a mongodb database externally for this, so just use the following Mongo URI in env:

   ```env
   MONGO_URI=mongodb//mongo:27017
   ```

2. Run the containers:

   ```sh
   docker compose up
   ```

## Contributions

🤝 Feel free to contribute any time by creating a pull request.

## License

This project is licensed under GNU GENERAL PUBLIC LICENSE V3.
This does not include the providers' sources, and, so we take no liability on their actions.