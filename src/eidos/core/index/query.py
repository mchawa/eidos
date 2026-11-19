"""Backward-compatible re-export facade for architectural index lexical search and graph traversal queries."""

from eidos.core.index.fts_query import (
    _ENTITY_ID_TOKEN_RE as _ENTITY_ID_TOKEN_RE,
)
from eidos.core.index.fts_query import (
    FTS_COLUMN_WEIGHTS as FTS_COLUMN_WEIGHTS,
)
from eidos.core.index.fts_query import (
    PRIMARY_TASK_SLICE_ENTITY_TYPES as PRIMARY_TASK_SLICE_ENTITY_TYPES,
)
from eidos.core.index.fts_query import (
    SECONDARY_TASK_SLICE_ENTITY_TYPES as SECONDARY_TASK_SLICE_ENTITY_TYPES,
)
from eidos.core.index.fts_query import (
    TASK_SLICE_ENTITY_TYPES as TASK_SLICE_ENTITY_TYPES,
)
from eidos.core.index.fts_query import (
    build_fts_match_query as build_fts_match_query,
)
from eidos.core.index.fts_query import (
    compound_identifier_subtokens as compound_identifier_subtokens,
)
from eidos.core.index.fts_query import (
    execute_fts_query as execute_fts_query,
)
from eidos.core.index.fts_query import (
    expand_architectural_synonyms as expand_architectural_synonyms,
)
from eidos.core.index.fts_query import (
    find_symbol_in_db as find_symbol_in_db,
)
from eidos.core.index.fts_query import (
    generate_repo_map_from_db as generate_repo_map_from_db,
)
from eidos.core.index.fts_query import (
    split_identifier as split_identifier,
)
from eidos.core.index.storage import (
    resolve_symbol_locations_in_db as resolve_symbol_locations_in_db,
)
from eidos.core.index.traversal import (
    _FILE_QUERY_EXTS as _FILE_QUERY_EXTS,
)
from eidos.core.index.traversal import (
    compute_delta_impact_in_db as compute_delta_impact_in_db,
)
from eidos.core.index.traversal import (
    execute_incoming_references as execute_incoming_references,
)
from eidos.core.index.traversal import (
    execute_k_hop_traversal as execute_k_hop_traversal,
)
from eidos.core.index.traversal import (
    query_spec_catalog_in_db as query_spec_catalog_in_db,
)
from eidos.core.index.traversal import (
    resolve_blast_radius_targets as resolve_blast_radius_targets,
)
from eidos.core.index.traversal import (
    resolve_symbol_or_path_in_db as resolve_symbol_or_path_in_db,
)

__all__ = [
    "FTS_COLUMN_WEIGHTS",
    "PRIMARY_TASK_SLICE_ENTITY_TYPES",
    "SECONDARY_TASK_SLICE_ENTITY_TYPES",
    "TASK_SLICE_ENTITY_TYPES",
    "build_fts_match_query",
    "compound_identifier_subtokens",
    "compute_delta_impact_in_db",
    "execute_fts_query",
    "execute_incoming_references",
    "execute_k_hop_traversal",
    "expand_architectural_synonyms",
    "find_symbol_in_db",
    "generate_repo_map_from_db",
    "query_spec_catalog_in_db",
    "resolve_blast_radius_targets",
    "resolve_symbol_locations_in_db",
    "resolve_symbol_or_path_in_db",
    "split_identifier",
]
