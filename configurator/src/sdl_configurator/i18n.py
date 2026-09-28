"""Small, explicit EN/FR message catalogs; user content is never translated."""
from importlib.resources import files
import json


class Messages:
    def __init__(self, locale: str = "en") -> None:
        self.locale = locale if locale in {"en", "fr"} else "en"
        self.catalog = json.loads(files("sdl_configurator").joinpath(f"locales/{self.locale}.json").read_text())

    def __call__(self, key: str, **values) -> str:
        return self.catalog[key].format(**values)
