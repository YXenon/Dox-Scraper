import json
from pathlib import Path
import typing

class ProgressTracker:
    def __init__(self, location: Path) -> None:
        if location.name.endswith(".json"):
            self.location = location
        else:
            self.location = location / "record.json"
        
        self.location.parent.mkdir(parents=True, exist_ok=True)
        
    def save(self, page: int, items: typing.List | None):
        save_data = {
            "page": page,
            "items": items
        }
        with open(self.location.resolve(), "w", encoding="utf-8") as file:
            json.dump(save_data, file)
    
    def load(self):
        if not self.location.exists():
            return 1, []
        with open(self.location.resolve(), "r", encoding="utf-8") as file:
            saved_data = json.load(file)
            return saved_data["page"] or 1, saved_data["items"]