## What is this directory ?

As the name suggests, it contains files for scraping.
Each source/provider may have their own technique of scraping so we have kept the scraper class independent in each provider.

## Use of each file

`launch.py`: This is the main entrypoint of scraper, `scraper_job()` being the function that's called at startup in a separate process.

`progress.py`: Used for saving and loading progress from last run, the file contains a progress tracker class.

`proxy.py`: Opens a optional http proxy that can be used by scrapers to download files by embedding urls in a network hosted page, helpful in scraping sites which prevent normal viewing.

`anilist.py`: Search for anime by order from anilist api. Default values on a page is set to `1` to make batch size small.

`converter.py`: Merging and converting file parts to mkv using ffmpeg, also merges subtitles along the way.

`providers/*.py`: All providers with their respectful url builders and scrapers.
