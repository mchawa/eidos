"""Validator for Eidos specifications against JSON/YAML schemas."""

from pathlib import Path
from typing import Any

import jsonschema
import yaml

from eidos.core.assets import get_v1_schemas_dir
from eidos.core.models import SpecDocument, ValidationReport
from eidos.core.parser import parse_spec_file

ENTITY_ID_PREFIXES = (
    "CNT-",
    "CMP-",
    "CTR-",
    "CAP-",
    "DOM-",
    "POL-",
    "PER-",
    "DLT-",
    "DS-",
    "UIC-",
    "SCR-",
    "CLT-",
    "FLOW-",
    "EXP-",
    "SYS-",
    "ARCH-",
)


def is_entity_id(val: Any) -> bool:
    """Return True if val looks like a standardized Eidos entity ID."""
    if not isinstance(val, str):
        return False
    val_clean = val.strip().upper()
    return any(val_clean.startswith(prefix) for prefix in ENTITY_ID_PREFIXES)

TYPE_TO_SCHEMA_NAME = {
    "product/system": "product/system.schema.yaml",
    "product/domain": "product/domain.schema.yaml",
    "product/capability": "product/capability.schema.yaml",
    "product/policy": "product/policy.schema.yaml",
    "product/persona": "product/persona.schema.yaml",
    "product/exploration": "product/exploration.schema.yaml",
    "delta/mutation": "delta/delta.schema.yaml",
    "tech/architecture": "tech/architecture.schema.yaml",
    "tech/container": "tech/container.schema.yaml",
    "tech/component": "tech/component.schema.yaml",
    "tech/contract": "tech/contract.schema.yaml",
    "design/system": "design/system.schema.yaml",
    "design/ui-component": "design/ui-component.schema.yaml",
    "design/screen": "design/screen.schema.yaml",
    "design/client": "design/client.schema.yaml",
    "design/flow": "design/flow.schema.yaml",
}


class SpecValidator:
    """Validates specification documents using bundled and custom schemas."""

    def __init__(self):
        self._schema_cache: dict[str, dict] = {}
        self._load_bundled_schemas()

    def _load_bundled_schemas(self):
        """Preload standard schemas recursively from the assets directory or package data."""
        try:
            schema_dir = get_v1_schemas_dir()
            for p in schema_dir.rglob("*.schema.yaml"):
                content = p.read_text(encoding="utf-8")
                data = yaml.safe_load(content)
                rel_path = str(p.relative_to(schema_dir))
                self._schema_cache[p.name] = data
                self._schema_cache[rel_path] = data
                self._schema_cache[f"schemas/v1/{rel_path}"] = data
                self._schema_cache[f"schemas/v1/{p.name}"] = data
            for p in schema_dir.rglob("*.schema.json"):
                content = p.read_text(encoding="utf-8")
                data = yaml.safe_load(content)
                rel_path = str(p.relative_to(schema_dir))
                self._schema_cache[p.name] = data
                self._schema_cache[rel_path] = data
                self._schema_cache[f"schemas/v1/{rel_path}"] = data
                self._schema_cache[f"schemas/v1/{p.name}"] = data
        except Exception:
            pass

    def _walk_traversable(self, node, rel_prefix: str):
        """Recursively walk an importlib Traversable directory."""
        for item in node.iterdir():
            sub_rel = f"{rel_prefix}/{item.name}".lstrip("/")
            if item.is_dir():
                self._walk_traversable(item, sub_rel)
            elif item.name.endswith(".schema.yaml") or item.name.endswith(".schema.json"):
                data = yaml.safe_load(item.read_text(encoding="utf-8"))
                self._schema_cache[item.name] = data
                self._schema_cache[sub_rel] = data
                self._schema_cache[f"schemas/v1/{sub_rel}"] = data
                self._schema_cache[f"schemas/v1/{item.name}"] = data

    def resolve_schema(self, doc: SpecDocument) -> dict | None:
        """Resolve the JSON schema for a document based on its type or explicit schema attribute."""
        # 1. Check explicit schema reference
        if doc.schema_ref:
            norm_ref = doc.schema_ref.replace(".spec/", "").strip()
            if norm_ref in self._schema_cache:
                return self._schema_cache[norm_ref]
            basename = Path(norm_ref).name
            if basename in self._schema_cache:
                return self._schema_cache[basename]

        # 2. Check document type mapping
        if doc.doc_type in TYPE_TO_SCHEMA_NAME:
            schema_name = TYPE_TO_SCHEMA_NAME[doc.doc_type]
            return self._schema_cache.get(schema_name)

        return None

    def validate_document(self, doc: SpecDocument) -> list[str]:
        """Validate a single parsed document against its schema."""
        if not doc.frontmatter:
            return doc.errors

        schema = self.resolve_schema(doc)
        if not schema:
            doc.errors.append(f"No schema found for document type '{doc.doc_type}'")
            return doc.errors

        validator = jsonschema.Draft202012Validator(schema)
        errors = []
        for err in validator.iter_errors(doc.frontmatter):
            loc = ".".join(str(p) for p in err.absolute_path)
            prefix = f"Property '{loc}'" if loc else "Root frontmatter"
            errors.append(f"{prefix}: {err.message}")

        doc.errors.extend(errors)
        return doc.errors

    def validate_directory(self, root_dir: Path) -> ValidationReport:
        """Scan a directory for .adoc files, parse, and validate each against its schema."""
        report = ValidationReport()
        adoc_files = sorted(root_dir.rglob("*.adoc"))

        for file_path in adoc_files:
            # Skip README, partial include files, symlinks, generated by-status views, and empty files
            if (
                file_path.name.lower() == "readme.adoc"
                or file_path.name.startswith("_")
                or "by-status" in file_path.parts
                or file_path.is_symlink()
                or not file_path.read_text(encoding="utf-8").strip()
            ):
                continue

            doc = parse_spec_file(file_path)
            self.validate_document(doc)
            report.documents.append(doc)
            if not doc.is_valid:
                report.schema_errors[str(file_path)] = doc.errors

        # Discover co-located component specs referenced by containers or located inside package_path
        repo_root = root_dir.parent if root_dir.name in ["eidos", ".spec"] else root_dir
        scanned_set = {str(d.file_path.resolve()) for d in report.documents}

        for doc in list(report.documents):
            if doc.doc_type == "tech/container" and doc.frontmatter:
                pkg_path = doc.frontmatter.get("package_path")
                # 1. Check explicit component refs in components array
                for comp in doc.frontmatter.get("components", []):
                    if isinstance(comp, dict) and comp.get("ref"):
                        ref_str = comp["ref"]
                        ref_file = (repo_root / ref_str).resolve()
                        if not ref_file.exists():
                            ref_file = (root_dir / ref_str).resolve()
                        if not ref_file.exists():
                            ref_file = (root_dir / "architecture" / "components" / Path(ref_str).name).resolve()
                        if not ref_file.exists():
                            report.broken_references.append(
                                f"Broken component ref in {doc.file_path.name}: cannot locate '{ref_str}'"
                            )
                        elif str(ref_file) not in scanned_set:
                            comp_doc = parse_spec_file(ref_file)
                            self.validate_document(comp_doc)
                            report.documents.append(comp_doc)
                            scanned_set.add(str(ref_file))
                            if not comp_doc.is_valid:
                                report.schema_errors[str(ref_file)] = comp_doc.errors

        # Cross-reference validation
        self._validate_cross_references(report, root_dir)

        return report

    def _validate_cross_references(self, report: ValidationReport, root_dir: Path):
        """Ensure all relative file links in YAML frontmatters and entity ID cross-references resolve."""
        # 1. Index all document IDs across the specification catalog and detect duplicate IDs
        report.entity_index = {}
        for doc in report.documents:
            if doc.doc_id:
                if doc.doc_id in report.entity_index:
                    prev_doc = report.entity_index[doc.doc_id]
                    report.broken_references.append(
                        f"Duplicate entity ID '{doc.doc_id}' found in {doc.file_path.name} (already defined in {prev_doc.file_path.name})"
                    )
                else:
                    report.entity_index[doc.doc_id] = doc

        catalog_ids = set(report.entity_index.keys())

        # Also register inline component IDs declared directly within containers in catalog_ids
        for doc in report.documents:
            if doc.doc_type == "tech/container" and doc.frontmatter:
                for comp in doc.frontmatter.get("components", []):
                    if isinstance(comp, dict) and comp.get("id"):
                        catalog_ids.add(str(comp["id"]).strip())

        for doc in report.documents:
            # 2. Check relative file paths ending in .adoc
            self._check_dict_references(doc.frontmatter, doc.file_path, root_dir, report)

            # 3. Check semantic entity ID cross-references
            self._validate_semantic_references(doc, catalog_ids, report, root_dir)

    def _validate_semantic_references(
        self,
        doc: SpecDocument,
        catalog_ids: set[str],
        report: ValidationReport,
        root_dir: Path,
    ):
        """Validate semantic entity ID references declared across specifications."""
        fm = doc.frontmatter or {}
        dtype = doc.doc_type

        # 1. SpecDeltas: affects.components, affects.contracts, affects.policies, affects.capabilities, affects.domains
        if dtype == "delta/mutation" or "affects" in fm:
            affects = fm.get("affects")
            if isinstance(affects, dict):
                for field_key, items in [
                    ("affects.components", affects.get("components")),
                    ("affects.contracts", affects.get("contracts")),
                    ("affects.policies", affects.get("policies")),
                    ("affects.capabilities", affects.get("capabilities")),
                    ("affects.domains", affects.get("domains")),
                ]:
                    if items:
                        item_list = [items] if isinstance(items, str) else items
                        if isinstance(item_list, list):
                            for item in item_list:
                                if (
                                    isinstance(item, str)
                                    and not item.endswith(".adoc")
                                    and (is_entity_id(item) or not ("/" in item or " " in item))
                                    and item.strip() not in catalog_ids
                                ):
                                    report.broken_references.append(
                                        f"Broken entity reference in {doc.file_path.name}: "
                                        f"'{item.strip()}' referenced in {field_key} does not exist in catalog"
                                    )

        # 2. Capabilities: domain / parent_domain, technical.components, technical.contracts
        if dtype == "product/capability":
            domain_val = fm.get("domain") or fm.get("parent_domain")
            if (
                domain_val
                and isinstance(domain_val, str)
                and not domain_val.endswith(".adoc")
                and domain_val.strip() not in catalog_ids
            ):
                report.broken_references.append(
                    f"Broken entity reference in {doc.file_path.name}: "
                    f"domain '{domain_val.strip()}' does not exist in catalog"
                )

            tech = fm.get("technical")
            if isinstance(tech, dict):
                components = tech.get("components", []) or []
                comp_list = [components] if isinstance(components, str) else components
                if isinstance(comp_list, list):
                    for comp in comp_list:
                        if (
                            isinstance(comp, str)
                            and (is_entity_id(comp) or comp.startswith("CMP-"))
                            and comp.strip() not in catalog_ids
                        ):
                            report.broken_references.append(
                                f"Broken entity reference in {doc.file_path.name}: "
                                f"component '{comp.strip()}' in technical.components does not exist in catalog"
                            )

                tech_contracts = tech.get("contracts")
                if isinstance(tech_contracts, dict):
                    for iface in tech_contracts.get("interfaces", []) or []:
                        if isinstance(iface, str) and is_entity_id(iface) and iface.strip() not in catalog_ids:
                            report.broken_references.append(
                                f"Broken entity reference in {doc.file_path.name}: "
                                f"contract '{iface.strip()}' in technical.contracts.interfaces does not exist in catalog"
                            )
                    for p in tech_contracts.get("provides", []) or []:
                        if (
                            isinstance(p, str)
                            and not p.endswith(".adoc")
                            and (is_entity_id(p) or not ("/" in p or " " in p))
                            and p.strip() not in catalog_ids
                        ):
                            report.broken_references.append(
                                f"Broken entity reference in {doc.file_path.name}: "
                                f"contract '{p.strip()}' in technical.contracts.provides does not exist in catalog"
                            )
                    for r in tech_contracts.get("requires", []) or []:
                        if (
                            isinstance(r, str)
                            and not r.endswith(".adoc")
                            and (is_entity_id(r) or not ("/" in r or " " in r))
                            and r.strip() not in catalog_ids
                        ):
                            report.broken_references.append(
                                f"Broken entity reference in {doc.file_path.name}: "
                                f"contract '{r.strip()}' in technical.contracts.requires does not exist in catalog"
                            )

        # 3. Contracts: owning_container / owning_component
        if dtype == "tech/contract":
            owning_ref = fm.get("owning_container") or fm.get("owning_component")
            if (
                owning_ref
                and isinstance(owning_ref, str)
                and not owning_ref.endswith(".adoc")
                and (is_entity_id(owning_ref) or owning_ref.startswith("CNT-") or owning_ref.startswith("CMP-"))
                and owning_ref.strip() not in catalog_ids
            ):
                report.broken_references.append(
                    f"Broken entity reference in {doc.file_path.name}: "
                    f"container/component '{owning_ref.strip()}' in owning_container/owning_component does not exist in catalog"
                )
            if owning_ref and str(owning_ref).startswith("CMP-"):
                err_msg = (
                    f"INV-INTERFACE-BOUNDARY-GOVERNANCE violation: Contract '{doc.doc_id}' specifies in-process component '{owning_ref}' as owner. "
                    f"Boundary Protocol Contracts (CTR-*) must be owned by a Container (CNT-*)."
                )
                doc.errors.append(err_msg)
                report.schema_errors.setdefault(str(doc.file_path), []).append(err_msg)

        # 4. Dedicated Components (tech/component)
        if dtype == "tech/component":
            # 1. INV-COMPONENT-CENTRALIZATION-MANDATE: MUST reside in eidos/architecture/components/
            rel_parts = doc.file_path.parts
            is_centralized = (
                "architecture" in rel_parts
                and "components" in rel_parts
                and any(p in ["eidos", ".spec"] for p in rel_parts)
            )
            if not is_centralized or "src" in rel_parts:
                err_msg = (
                    f"INV-COMPONENT-CENTRALIZATION-MANDATE violation: Component manifest '{doc.doc_id}' must reside "
                    f"centrally in 'eidos/architecture/components/'. Eidos specifications must never be placed inside source code directories."
                )
                doc.errors.append(err_msg)
                report.schema_errors.setdefault(str(doc.file_path), []).append(err_msg)

            # 2. INV-COMPONENT-CONTAINER-AFFINITY: parent container must exist
            cnt_ref = fm.get("container")
            if cnt_ref:
                if cnt_ref not in catalog_ids:
                    report.broken_references.append(
                        f"INV-COMPONENT-CONTAINER-AFFINITY violation in {doc.file_path.name}: "
                        f"Component '{doc.doc_id}' references container '{cnt_ref}' which does not exist in catalog"
                    )

            # 3. INV-INTERFACE-BOUNDARY-GOVERNANCE: Components cannot declare boundary contracts
            if fm.get("contracts"):
                err_msg = (
                    f"INV-INTERFACE-BOUNDARY-GOVERNANCE violation: Component '{doc.doc_id}' cannot declare boundary protocol contracts. "
                    f"Use 'interfaces.provides' for in-process APIs."
                )
                doc.errors.append(err_msg)
                report.schema_errors.setdefault(str(doc.file_path), []).append(err_msg)

            # Check consumed components / modules
            ifaces = fm.get("interfaces", {})
            if isinstance(ifaces, dict):
                for consumed in ifaces.get("consumes", []):
                    if isinstance(consumed, str) and consumed.startswith("CMP-") and consumed not in catalog_ids:
                        report.broken_references.append(
                            f"Broken entity reference in {doc.file_path.name}: "
                            f"consumed component '{consumed}' in interfaces.consumes does not exist in catalog"
                        )

        # 5. Containers: contracts.provides, contracts.requires/consumes, dependencies.internal_containers/internal_components, domain_binding
        if dtype in ("tech/container", "tech/component"):
            contracts = fm.get("contracts")
            if isinstance(contracts, dict):
                provides = contracts.get("provides", []) or []
                prov_list = [provides] if isinstance(provides, str) else provides
                if isinstance(prov_list, list):
                    for item in prov_list:
                        if (
                            isinstance(item, str)
                            and not item.endswith(".adoc")
                            and (is_entity_id(item) or item.startswith("CTR-") or not ("/" in item or " " in item))
                            and item.strip() not in catalog_ids
                        ):
                            report.broken_references.append(
                                f"Broken entity reference in {doc.file_path.name}: "
                                f"contract '{item.strip()}' in contracts.provides does not exist in catalog"
                            )

                for f_key in ("requires", "consumes"):
                    reqs = contracts.get(f_key, []) or []
                    req_list = [reqs] if isinstance(reqs, str) else reqs
                    if isinstance(req_list, list):
                        for item in req_list:
                            if (
                                isinstance(item, str)
                                and not item.endswith(".adoc")
                                and (is_entity_id(item) or item.startswith("CTR-") or not ("/" in item or " " in item))
                                and item.strip() not in catalog_ids
                            ):
                                report.broken_references.append(
                                    f"Broken entity reference in {doc.file_path.name}: "
                                    f"contract '{item.strip()}' in contracts.{f_key} does not exist in catalog"
                                )

            deps = fm.get("dependencies")
            if isinstance(deps, dict):
                int_comps = deps.get("internal_containers", []) or deps.get("internal_components", []) or []
                comp_list = [int_comps] if isinstance(int_comps, str) else int_comps
                if isinstance(comp_list, list):
                    for item in comp_list:
                        if isinstance(item, str) and not item.endswith(".adoc") and item.strip() not in catalog_ids:
                            report.broken_references.append(
                                f"Broken entity reference in {doc.file_path.name}: "
                                f"container/component '{item.strip()}' in dependencies does not exist in catalog"
                            )

            db = fm.get("domain_binding")
            if (
                db
                and isinstance(db, str)
                and not db.endswith(".adoc")
                and (is_entity_id(db) or db.startswith("DOM-"))
                and db.strip() not in catalog_ids
            ):
                report.broken_references.append(
                    f"Broken entity reference in {doc.file_path.name}: "
                    f"domain '{db.strip()}' in domain_binding does not exist in catalog"
                )

        # 5. Domains: technical_mapping.dependencies, technical_mapping.components
        if dtype == "product/domain":
            tech_map = fm.get("technical_mapping")
            if isinstance(tech_map, dict):
                for dep in tech_map.get("dependencies", []) or []:
                    if (
                        isinstance(dep, str)
                        and not dep.endswith(".adoc")
                        and (is_entity_id(dep) or dep.startswith("DOM-"))
                        and dep.strip() not in catalog_ids
                    ):
                        report.broken_references.append(
                            f"Broken entity reference in {doc.file_path.name}: "
                            f"domain '{dep.strip()}' in technical_mapping.dependencies does not exist in catalog"
                        )
                for cmp_item in tech_map.get("components", []) or []:
                    if (
                        isinstance(cmp_item, str)
                        and not cmp_item.endswith(".adoc")
                        and is_entity_id(cmp_item)
                        and cmp_item.strip() not in catalog_ids
                    ):
                        report.broken_references.append(
                            f"Broken entity reference in {doc.file_path.name}: "
                            f"component '{cmp_item.strip()}' in technical_mapping.components does not exist in catalog"
                        )

        # 6. Explorations: decision.resulting_delta_id
        if dtype == "product/exploration":
            decision = fm.get("decision")
            if isinstance(decision, dict):
                resulting_delta = decision.get("resulting_delta_id")
                if (
                    resulting_delta
                    and isinstance(resulting_delta, str)
                    and not resulting_delta.endswith(".adoc")
                    and (is_entity_id(resulting_delta) or resulting_delta.startswith("DLT-"))
                    and resulting_delta.strip() not in catalog_ids
                ):
                    report.broken_references.append(
                        f"Broken entity reference in {doc.file_path.name}: "
                        f"delta '{resulting_delta.strip()}' in decision.resulting_delta_id does not exist in catalog"
                    )

        # 7. Relational design and business references
        for field_key in ("design_system", "extends_system", "implements_component", "container", "client"):
            val = fm.get(field_key)
            if (
                val
                and isinstance(val, str)
                and not val.endswith(".adoc")
                and is_entity_id(val)
                and val.strip() not in catalog_ids
            ):
                report.broken_references.append(
                    f"Broken entity reference in {doc.file_path.name}: "
                    f"entity '{val.strip()}' in {field_key} does not exist in catalog"
                )

        for client_ref in fm.get("clients", []) or []:
            if (
                isinstance(client_ref, str)
                and not client_ref.endswith(".adoc")
                and is_entity_id(client_ref)
                and client_ref.strip() not in catalog_ids
            ):
                report.broken_references.append(
                    f"Broken entity reference in {doc.file_path.name}: "
                    f"client '{client_ref.strip()}' in clients does not exist in catalog"
                )

        biz = fm.get("business")
        if isinstance(biz, dict):
            for p in biz.get("personas", []) or []:
                if isinstance(p, str) and not p.endswith(".adoc") and is_entity_id(p) and p.strip() not in catalog_ids:
                    report.broken_references.append(
                        f"Broken entity reference in {doc.file_path.name}: "
                        f"persona '{p.strip()}' in business.personas does not exist in catalog"
                    )
            for p in biz.get("policies", []) or []:
                if isinstance(p, str) and not p.endswith(".adoc") and is_entity_id(p) and p.strip() not in catalog_ids:
                    report.broken_references.append(
                        f"Broken entity reference in {doc.file_path.name}: "
                        f"policy '{p.strip()}' in business.policies does not exist in catalog"
                    )

        exp = fm.get("experience")
        if isinstance(exp, dict):
            for exp_key in ("design_systems", "screens", "ui_components"):
                for item in exp.get(exp_key, []) or []:
                    if (
                        isinstance(item, str)
                        and not item.endswith(".adoc")
                        and is_entity_id(item)
                        and item.strip() not in catalog_ids
                    ):
                        report.broken_references.append(
                            f"Broken entity reference in {doc.file_path.name}: "
                            f"entity '{item.strip()}' in experience.{exp_key} does not exist in catalog"
                        )

    def _check_dict_references(self, val, source_file: Path, root_dir: Path, report: ValidationReport):
        """Recursively check string fields ending in .adoc to verify they exist."""
        if isinstance(val, dict):
            for v in val.values():
                self._check_dict_references(v, source_file, root_dir, report)
        elif isinstance(val, list):
            for item in val:
                self._check_dict_references(item, source_file, root_dir, report)
        elif isinstance(val, str) and val.endswith(".adoc"):
            # Resolve relative to repo root or doc folder
            ref_path_from_root = root_dir.parent / val if root_dir.name in ["eidos", ".spec"] else root_dir / val
            ref_path_from_doc = source_file.parent / val
            if not (ref_path_from_root.exists() or ref_path_from_doc.exists()):
                report.broken_references.append(f"Broken reference in {source_file.name}: cannot locate '{val}'")

