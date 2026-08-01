"""CRM schema registry: loads all entity/field definitions from bundled CSV.

Provides universal field resolution so Hermes can look up any field on any entity
without hardcoded aliases.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent / "data"
_CSV_PATH = _DATA_DIR / "custom-fields-camelcase-audit.csv"


@dataclass
class FieldDef:
    entity: str
    field_name: str
    field_type: str
    read_only: bool = False
    audited: bool = False


@dataclass
class EntitySchema:
    name: str
    fields: dict[str, FieldDef] = field(default_factory=dict)


class SchemaRegistry:
    """Singleton-style registry loaded from the bundled CSV."""

    def __init__(self) -> None:
        self._entities: dict[str, EntitySchema] = {}
        self._all_fields: dict[str, list[FieldDef]] = {}
        self._loaded = False

    def load(self, csv_path: Path | str | None = None) -> None:
        path = Path(csv_path) if csv_path else _CSV_PATH
        if not path.exists():
            log.warning("Schema CSV not found at %s", path)
            return
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                entity = row.get("entity", "").strip()
                fname = row.get("field_name", "").strip()
                ftype = row.get("type", "").strip()
                ro = row.get("read_only", "").strip().lower() == "true"
                audited = row.get("audited", "").strip().lower() == "true"
                if not entity or not fname:
                    continue
                fd = FieldDef(entity=entity, field_name=fname, field_type=ftype, read_only=ro, audited=audited)
                if entity not in self._entities:
                    self._entities[entity] = EntitySchema(name=entity)
                self._entities[entity].fields[fname] = fd
                self._all_fields.setdefault(fname.lower(), []).append(fd)
        self._loaded = True
        total = sum(len(e.fields) for e in self._entities.values())
        log.info("Schema registry loaded: %d entities, %d fields", len(self._entities), total)

    def ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def get_entities(self) -> list[str]:
        self.ensure_loaded()
        return sorted(self._entities.keys())

    def get_fields(self, entity: str) -> dict[str, FieldDef]:
        self.ensure_loaded()
        schema = self._entities.get(entity)
        return schema.fields if schema else {}

    def field_type(self, entity: str, field_name: str) -> str | None:
        self.ensure_loaded()
        schema = self._entities.get(entity)
        if schema and field_name in schema.fields:
            return schema.fields[field_name].field_type
        return None

    def find_field(self, name: str) -> list[FieldDef]:
        """Find which entities have a field matching this name (case-insensitive, fuzzy)."""
        self.ensure_loaded()
        key = name.lower().replace(" ", "").replace("_", "")

        exact = self._all_fields.get(name.lower())
        if exact:
            return exact

        matches: list[FieldDef] = []
        for field_key, defs in self._all_fields.items():
            normalized = field_key.replace("_", "")
            if key in normalized or normalized in key:
                matches.extend(defs)
        return matches

    def resolve_field_for_entity(self, field_hint: str, entity_hint: str | None = None) -> list[FieldDef]:
        """Resolve a natural-language field name to actual CRM fields, optionally scoped to an entity."""
        self.ensure_loaded()
        candidates = self.find_field(field_hint)
        if entity_hint:
            entity_upper = entity_hint.strip().title()
            scoped = [f for f in candidates if f.entity == entity_upper]
            if scoped:
                return scoped
        return candidates

    def get_required_fields(self, entity: str) -> list[str]:
        """Return fields that are marked as audited (i.e. should be populated)."""
        self.ensure_loaded()
        schema = self._entities.get(entity)
        if not schema:
            return []
        return [f.field_name for f in schema.fields.values() if f.audited]

    def get_entity_field_count(self, entity: str) -> int:
        self.ensure_loaded()
        schema = self._entities.get(entity)
        return len(schema.fields) if schema else 0


_registry: SchemaRegistry | None = None


def get_registry() -> SchemaRegistry:
    """Module-level singleton."""
    global _registry
    if _registry is None:
        _registry = SchemaRegistry()
        _registry.load()
    return _registry
