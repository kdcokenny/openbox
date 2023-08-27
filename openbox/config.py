"""CodeBox API Config:

Automatically loads environment variables from .env file
"""

from dotenv import load_dotenv
from pydantic_settings import BaseSettings

# .env file
load_dotenv("./.env")


class CodeBoxSettings(BaseSettings):  # type: ignore
    """CodeBox API Config."""

    VERBOSE: bool = False
    SHOW_INFO: bool = True


settings = CodeBoxSettings()
