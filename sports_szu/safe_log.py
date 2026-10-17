"""Bounded UTF-8 diagnostic files; callers provide summaries, never raw HTTP data."""
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def make_handler(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(directory / 'runtime.log', maxBytes=5 * 1024 * 1024,
                                  backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    return handler


def write(handler, moment, level, message):
    # One physical line per event, even for user-entered schedule names.
    text = str(message).replace('\r', ' ').replace('\n', ' ')[:2000]
    handler.handle(logging.LogRecord('sports-szu', logging.INFO, '', 0,
        f'{moment.isoformat(timespec="seconds")} [{level.upper()}] {text}', (), None))
