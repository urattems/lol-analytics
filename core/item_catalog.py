"""Patch-matched, explicitly downloaded item metadata. Analytical reads are offline."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
import time

from core.db import Database
from core.network import environment_client

CATALOG_SCHEMA = 1
PATCH = re.compile(r'\d{1,3}\.\d{1,3}')
VERSION = re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}')
BASE_URL = 'https://ddragon.leagueoflegends.com'


class CatalogUnavailable(ValueError):
    """A requested patch has no usable catalog; never substitute the latest patch."""


def integer(value, *, minimum=0):
    return value if type(value) is int and minimum <= value <= 2_147_483_647 else None


def item_ids(value):
    if not isinstance(value, list) or len(value) > 64:
        raise ValueError('Invalid recipe')
    ids = tuple(int(v) for v in value if isinstance(v, str) and re.fullmatch(r'[1-9]\d{0,9}', v))
    if len(ids) != len(value) or any(integer(v, minimum=1) is None for v in ids):
        raise ValueError('Invalid recipe')
    return ids


@dataclass(frozen=True)
class ItemDefinition:
    item_id: int
    name: str
    total: int
    base: int
    purchasable: bool
    on_map: bool
    in_store: bool
    consumed: bool
    tags: tuple[str, ...]
    recipe: tuple[int, ...]
    upgrades: tuple[int, ...]
    required_champion: str = ''
    restricted: bool = False


@dataclass(frozen=True)
class ItemCatalog:
    patch: str
    version: str
    fetched_at: int
    items: dict[int, ItemDefinition]

    def classify(self, item_id: int, champion: str) -> str:
        """Conservative recipe classification, not a tier/price-based build score."""
        item = self.items.get(item_id)
        if item is None or not item.on_map:
            return 'unknown'
        if item.consumed or set(item.tags) & {'Consumable', 'Trinket'}:
            return 'excluded'
        if item.restricted or (item.required_champion and item.required_champion != champion):
            return 'excluded'
        if 'Boots' in item.tags:
            depth = self._boot_depth(item_id, frozenset())
            if depth is None:
                return 'unknown'
            if depth == 2 and self._buyable(item):
                return 'boots_t2'
            return 'boots_t3' if depth >= 3 else 'excluded'
        if not self._buyable(item) or not item.recipe:
            return 'excluded'  # free quest/transform, starting item or special item
        if any(i not in self.items for i in (*item.recipe, *item.upgrades)):
            return 'unknown'
        # Free automatic evolutions and items from other maps do not make a
        # purchasable completed item a component (e.g. tear upgrades).
        if any(self._buyable(self.items[i]) for i in item.upgrades):
            return 'component'
        return 'major'

    @staticmethod
    def _buyable(item: ItemDefinition) -> bool:
        return item.on_map and item.in_store and item.purchasable and item.base > 0 and item.total > 0 and not item.consumed

    def _boot_depth(self, identifier: int, seen: frozenset[int]) -> int | None:
        if identifier in seen or len(seen) >= 32:
            return None
        item = self.items.get(identifier)
        if item is None or any(i not in self.items for i in item.recipe):
            return None
        parents = [i for i in item.recipe if 'Boots' in self.items[i].tags]
        depths = [self._boot_depth(i, seen | {identifier}) for i in parents]
        if None in depths:
            return None
        return 1 + max(depths, default=0)


def parse_catalog(payload, patch: str, version: str, fetched_at: int) -> ItemCatalog:
    if (not isinstance(patch, str) or not PATCH.fullmatch(patch) or not isinstance(version, str)
            or not VERSION.fullmatch(version) or version.rsplit('.', 1)[0] != patch):
        raise CatalogUnavailable('Le catalogue ne correspond pas au patch demandé.')
    if type(fetched_at) is not int or not 0 <= fetched_at <= 253_402_300_799:
        raise CatalogUnavailable('Date de catalogue inexploitable.')
    data = payload.get('data') if isinstance(payload, dict) and payload.get('version') == version else None
    if not isinstance(data, dict) or not 1 <= len(data) <= 5000:
        raise CatalogUnavailable('Catalogue d’objets inexploitable pour ce patch.')
    items = {}
    for key, raw in data.items():
        try:
            identifier = item_ids([key])[0]
            gold, maps = raw['gold'], raw['maps']
            tags = raw.get('tags', [])
            name = raw.get('name')
            if not isinstance(name, str) or not 1 <= len(name) <= 160:
                raise ValueError()
            if not isinstance(tags, list) or len(tags) > 64 or any(not isinstance(t, str) or len(t) > 64 for t in tags):
                raise ValueError()
            total, base = integer(gold['total']), integer(gold['base'])
            flags = (gold['purchasable'], maps.get('11', False), raw.get('inStore', True), raw.get('consumed', False))
            if total is None or base is None or any(type(v) is not bool for v in flags):
                raise ValueError()
            required = raw.get('requiredChampion', '')
            if not isinstance(required, str) or len(required) > 80:
                raise ValueError()
            items[identifier] = ItemDefinition(identifier, name, total, base, *flags, tuple(tags),
                item_ids(raw.get('from', [])), item_ids(raw.get('into', [])), required,
                bool(raw.get('requiredAlly') or raw.get('specialRecipe')))
        except (KeyError, ValueError, TypeError, IndexError, AttributeError):
            continue  # malformed item remains unknown, not silently a component
    if not items:
        raise CatalogUnavailable('Aucun objet exploitable dans ce catalogue.')
    return ItemCatalog(patch, version, fetched_at, items)


def save_catalog(database: Database, catalog: ItemCatalog) -> None:
    # Store only validated analytic fields: no HTML, opaque extensions or user IDs.
    data = json.dumps([asdict(item) for item in catalog.items.values()], ensure_ascii=False, allow_nan=False)
    with database.connection() as connection:
        connection.execute('''INSERT INTO item_catalogs(patch, version, fetched_at, schema_version, catalog_json)
            VALUES (?, ?, ?, ?, ?) ON CONFLICT(patch) DO UPDATE SET version=excluded.version,
            fetched_at=excluded.fetched_at, schema_version=excluded.schema_version, catalog_json=excluded.catalog_json''',
            (catalog.patch, catalog.version, catalog.fetched_at, CATALOG_SCHEMA, data))


def load_catalogs(database: Database, patches: list[str]) -> dict[str, ItemCatalog]:
    """One local read, no HTTP and no migration on lookup."""
    wanted = sorted({p for p in patches if isinstance(p, str) and PATCH.fullmatch(p)})
    if not wanted:
        return {}
    with database.connection() as connection:
        rows = connection.execute('SELECT * FROM item_catalogs').fetchall()
    catalogs = {}
    for row in rows:
        if row['patch'] not in wanted or row['schema_version'] != CATALOG_SCHEMA:
            continue
        try:
            # Revalidate the private cache too, through the same external parser.
            stored = json.loads(row['catalog_json'])
            if not isinstance(stored, list) or not 1 <= len(stored) <= 5000:
                continue
            if (any(not isinstance(i, dict) or integer(i.get('item_id'), minimum=1) is None
                    or type(i.get('restricted')) is not bool for i in stored)
                    or len({i['item_id'] for i in stored}) != len(stored)):
                continue
            data = {str(i['item_id']): {
                'name': i['name'], 'gold': {'total': i['total'], 'base': i['base'], 'purchasable': i['purchasable']},
                'maps': {'11': i['on_map']}, 'inStore': i['in_store'], 'consumed': i['consumed'], 'tags': i['tags'],
                'from': [str(v) for v in i['recipe']], 'into': [str(v) for v in i['upgrades']],
                'requiredChampion': i['required_champion'], 'requiredAlly': i['restricted'],
            } for i in stored}
            parsed = parse_catalog({'version': row['version'], 'data': data}, row['patch'], row['version'], row['fetched_at'])
            if len(parsed.items) == len(stored):
                catalogs[row['patch']] = parsed
        except (KeyError, ValueError, TypeError):
            continue
    return catalogs


def fetch_catalog(database: Database, patch: str, *, transport=None, clock=time.time) -> ItemCatalog:
    """Explicit user action: fetch one exact patch, then commit its validated catalog."""
    if not isinstance(patch, str) or not PATCH.fullmatch(patch):
        raise CatalogUnavailable('Patch invalide.')
    with environment_client(timeout=8.0, transport=transport) as client:
        response = client.get(BASE_URL + '/api/versions.json')
        response.raise_for_status()
        versions = response.json()
        candidates = [v for v in versions if isinstance(v, str) and VERSION.fullmatch(v) and v.rsplit('.', 1)[0] == patch] if isinstance(versions, list) else []
        if not candidates:
            raise CatalogUnavailable('Ce patch n’a pas de catalogue Data Dragon disponible.')
        version = max(candidates, key=lambda v: tuple(map(int, v.split('.'))))
        response = client.get(f'{BASE_URL}/cdn/{version}/data/fr_FR/item.json')
        response.raise_for_status()
        catalog = parse_catalog(response.json(), patch, version, int(clock()))
    save_catalog(database, catalog)
    return catalog
