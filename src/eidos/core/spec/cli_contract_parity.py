"""Bidirectional CLI interface contract parity verification across Typer, Click, and argparse (CMP-CONTRACT-PARITY-VALIDATOR)."""

import ast
import contextlib
import re
from pathlib import Path

from eidos.core.index.ast_preprocess import _parse_python_ast as _parse_python_ast
from eidos.core.spec.models import SpecDocument, ValidationReport
from eidos.core.workspace.project import resolve_spec_and_repo_roots


def strip_asciidoc_comments(body: str) -> str:
    """Strip AsciiDoc block comments (//// ... ////) and single-line comments (// ...) from contract body."""
    cleaned = re.sub(
        r"^[ \t]*/{4,}[ \t]*\r?\n(?:.*?\r?\n)?[ \t]*/{4,}[ \t]*$",
        "",
        body,
        flags=re.MULTILINE | re.DOTALL,
    )
    return re.sub(r"^[ \t]*//[^\r\n]*$", "", cleaned, flags=re.MULTILINE)


def verify_cli_contract_commands(doc: SpecDocument, cli_file: Path, report: ValidationReport) -> None:
    """Verify bidirectional CLI command parity across Typer (including sub-apps), Click, and argparse entrypoints (DLT-2026-082)."""
    try:
        source = cli_file.read_text(encoding="utf-8")
        tree = _parse_python_ast(source)
    except (OSError, SyntaxError, ValueError):
        return

    source_lower = source.lower()
    has_typer = "typer" in source_lower
    has_click = "click" in source_lower
    has_argparse = "argparse" in source_lower
    if not (has_typer or has_click or has_argparse):
        return

    body = doc.body or ""
    _, cli_repo_root = resolve_spec_and_repo_roots(None, cli_file)

    typer_module_aliases: set[str] = set()
    typer_class_aliases: set[str] = set()
    click_module_aliases: set[str] = set()
    click_group_decorators: set[str] = set()
    click_cmd_decorators: set[str] = set()

    def _register_typer_aliases(mod_ast: ast.AST) -> None:
        for n_imp in ast.walk(mod_ast):
            if isinstance(n_imp, ast.Import):
                for al in n_imp.names:
                    if al.name == "typer" or al.name.startswith("typer."):
                        typer_module_aliases.add(al.asname or al.name.split(".")[0])
            elif (
                isinstance(n_imp, ast.ImportFrom)
                and n_imp.module
                and (n_imp.module == "typer" or n_imp.module.startswith("typer."))
            ):
                for al in n_imp.names:
                    if al.name == "Typer":
                        typer_class_aliases.add(al.asname or al.name)
                    elif al.name == "typer":
                        typer_module_aliases.add(al.asname or al.name)

    _register_typer_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "click" or alias.name.startswith("click."):
                    click_module_aliases.add(alias.asname or alias.name.split(".")[0])
        elif (
            isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "click" or node.module.startswith("click."))
        ):
            for alias in node.names:
                if alias.name == "group":
                    click_group_decorators.add(alias.asname or alias.name)
                elif alias.name == "command":
                    click_cmd_decorators.add(alias.asname or alias.name)

    def _is_typer_ctor(call_node: ast.AST | None) -> bool:
        if not isinstance(call_node, ast.Call):
            return False
        func = call_node.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return func.value.id in typer_module_aliases and func.attr == "Typer"
        if isinstance(func, ast.Name):
            return func.id in typer_class_aliases
        return False

    def _extract_typer_ctor_name(call_node: ast.AST | None) -> str | None:
        if not isinstance(call_node, ast.Call):
            return None
        for kw in call_node.keywords:
            if (
                kw.arg == "name"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
                and kw.value.value.strip()
            ):
                return kw.value.value.strip()
        if (
            call_node.args
            and isinstance(call_node.args[0], ast.Constant)
            and isinstance(call_node.args[0].value, str)
            and call_node.args[0].value.strip()
        ):
            return call_node.args[0].value.strip()
        return None

    def _collect_typer_vars(scope_node: ast.AST) -> tuple[set[str], dict[str, str]]:
        vars_found: set[str] = set()
        ctor_names: dict[str, str] = {}
        for n in ast.walk(scope_node):
            if isinstance(n, ast.Assign) and _is_typer_ctor(n.value):
                c_name = _extract_typer_ctor_name(n.value)
                for target in n.targets:
                    if isinstance(target, ast.Name):
                        vars_found.add(target.id)
                        if c_name:
                            ctor_names[target.id] = c_name
            elif isinstance(n, ast.AnnAssign) and _is_typer_ctor(n.value) and isinstance(n.target, ast.Name):
                vars_found.add(n.target.id)
                c_name = _extract_typer_ctor_name(n.value)
                if c_name:
                    ctor_names[n.target.id] = c_name
        return vars_found, ctor_names

    typer_app_vars, root_ctor_names = (
        _collect_typer_vars(tree) if (typer_module_aliases or typer_class_aliases) else (set(), {})
    )

    # Preserve DLT-2026-049 compatibility: for non-Typer-app files (Click/argparse), only enforce CLI command
    # parity when the contract body declares command sections (===), tables (|===), or backtick command literals.
    if not typer_app_vars and not any(tok in body for tok in ("===", "|===", "`")):
        return

    def _extract_command_name_from_call(cmd_call: ast.Call, fallback_func_name: str | None) -> str | None:
        hidden = any(
            kw.arg == "hidden" and isinstance(kw.value, ast.Constant) and kw.value.value is True
            for kw in cmd_call.keywords
        )
        if hidden:
            return None
        name = next(
            (
                kw.value.value
                for kw in cmd_call.keywords
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
            ),
            None,
        )
        if (
            not name
            and cmd_call.args
            and isinstance(cmd_call.args[0], ast.Constant)
            and isinstance(cmd_call.args[0].value, str)
        ):
            name = cmd_call.args[0].value
        if name:
            return name
        if fallback_func_name:
            return fallback_func_name.replace("_", "-")
        return None

    commands: set[str] = set()
    subcommands_by_group: dict[str, set[str]] = {}
    nested_subcommands_by_group: dict[tuple[str, str], set[str]] = {}
    cmd_pos_args: dict[str, list[tuple[str, str | None, bool]]] = {}
    sub_pos_args: dict[tuple[str, ...], list[tuple[str, str | None, bool]]] = {}

    def _scan_module_imports(
        mod_ast: ast.AST,
    ) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
        mod_aliases: dict[str, str] = {}
        sym_imports: dict[str, tuple[str, str]] = {}
        for n_i in ast.walk(mod_ast):
            if isinstance(n_i, ast.ImportFrom):
                full_mod = (n_i.module or "").strip(".")
                for al in n_i.names:
                    local_name = al.asname or al.name
                    cand_submod = f"{full_mod}.{al.name}".strip(".") if full_mod else al.name
                    mod_aliases[local_name] = cand_submod
                    if full_mod:
                        sym_imports[local_name] = (full_mod, al.name)
            elif isinstance(n_i, ast.Import):
                for al in n_i.names:
                    local_name = al.asname or al.name.rsplit(".", 1)[-1]
                    mod_aliases[local_name] = al.name
        return mod_aliases, sym_imports

    sibling_module_aliases, imported_symbol_modules = _scan_module_imports(tree)
    sibling_trees: dict[str, ast.Module] = {}

    def _get_sibling_tree(mod_ref: str) -> ast.Module | None:
        if not mod_ref:
            return None
        if mod_ref in sibling_trees:
            return sibling_trees[mod_ref]
        mod_clean = mod_ref.strip(".")
        mod_parts = [p for p in mod_clean.split(".") if p]
        if not mod_parts:
            return None
        mod_leaf = mod_parts[-1]
        candidates: list[Path] = [
            cli_file.parent / f"{mod_leaf}.py",
            cli_file.parent / mod_leaf / "__init__.py",
        ]
        for i in range(len(mod_parts)):
            rel_sub = Path(*mod_parts[i:])
            for search_base in (cli_file.parent, cli_repo_root / "src", cli_repo_root):
                candidates.append(search_base / rel_sub.with_suffix(".py"))
                candidates.append(search_base / rel_sub / "__init__.py")
        seen_cands: set[Path] = set()
        for cand_py in candidates:
            if cand_py in seen_cands:
                continue
            seen_cands.add(cand_py)
            if cand_py.is_file() and cand_py.resolve() != cli_file.resolve():
                with contextlib.suppress(OSError, SyntaxError, ValueError):
                    parsed = _parse_python_ast(cand_py.read_text(encoding="utf-8"))
                    _register_typer_aliases(parsed)
                    sibling_trees[mod_ref] = parsed
                    sibling_trees[mod_leaf] = parsed
                    return parsed
        return None

    if typer_app_vars:

        def _resolve_sub_typer_name(
            call_node: ast.Call,
            sub_var: str | None,
            ctor_names_map: dict[str, str],
            imp_map: dict[str, tuple[str, str]],
            attr_target: tuple[str, str] | None = None,
        ) -> str | None:
            sub_name = next(
                (
                    kw.value.value
                    for kw in call_node.keywords
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                ),
                None,
            )
            if (
                not sub_name
                and len(call_node.args) >= 2
                and isinstance(call_node.args[1], ast.Constant)
                and isinstance(call_node.args[1].value, str)
            ):
                sub_name = call_node.args[1].value
            if not sub_name and attr_target is not None:
                sib_mod_ref, orig_attr = attr_target
                sib_ast = _get_sibling_tree(sib_mod_ref)
                if sib_ast is not None:
                    _, sib_ctors = _collect_typer_vars(sib_ast)
                    sub_name = sib_ctors.get(orig_attr)
            if not sub_name and sub_var:
                sub_name = ctor_names_map.get(sub_var)
                if not sub_name and sub_var in imp_map:
                    sib_mod, orig_var = imp_map[sub_var]
                    sib_ast = _get_sibling_tree(sib_mod)
                    if sib_ast is not None:
                        _, sib_ctors = _collect_typer_vars(sib_ast)
                        sub_name = sib_ctors.get(orig_var)
            return sub_name

        mounted_sub_apps: dict[str, str | None] = {}  # var_name -> group_name (or None if unnamed)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in typer_app_vars
                and node.func.attr == "add_typer"
                and node.args
            ):
                arg0 = node.args[0]
                if isinstance(arg0, ast.Name):
                    sub_var = arg0.id
                    sub_name = _resolve_sub_typer_name(node, sub_var, root_ctor_names, imported_symbol_modules)
                    mounted_sub_apps[sub_var] = sub_name
                elif isinstance(arg0, ast.Attribute) and isinstance(arg0.value, ast.Name):
                    mod_ref = sibling_module_aliases.get(arg0.value.id, arg0.value.id)
                    sub_name = _resolve_sub_typer_name(
                        node, None, root_ctor_names, imported_symbol_modules, attr_target=(mod_ref, arg0.attr)
                    )
                    mounted_sub_apps[f"{arg0.value.id}.{arg0.attr}"] = sub_name

        root_app_vars = typer_app_vars - set(mounted_sub_apps) or (
            {"app"} if "app" in typer_app_vars else typer_app_vars
        )

        def _find_typer_param_call(node: ast.AST | None, attr_name: str = "Argument") -> ast.Call | None:
            if node is None:
                return None
            for child in ast.walk(node):
                if isinstance(child, ast.Call):
                    func_node = child.func
                    if (isinstance(func_node, ast.Attribute) and func_node.attr == attr_name) or (
                        isinstance(func_node, ast.Name) and func_node.id == attr_name
                    ):
                        return child
            return None

        def _is_typer_context_annotation(node: ast.AST | None) -> bool:
            if node is None:
                return False
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and child.id == "Context":
                    return True
                if isinstance(child, ast.Attribute) and child.attr == "Context":
                    return True
            return False

        def _extract_typer_positional_args(
            fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> list[tuple[str, str | None, bool]]:
            pos_out: list[tuple[str, str | None, bool]] = []
            args_list = list(fn_node.args.args)
            defaults_list = list(fn_node.args.defaults)
            default_offset = len(args_list) - len(defaults_list)
            for idx_a, arg_node in enumerate(args_list):
                if arg_node.arg in ("self", "cls") or _is_typer_context_annotation(arg_node.annotation):
                    continue
                def_idx = idx_a - default_offset
                def_val = defaults_list[def_idx] if def_idx >= 0 else None
                arg_call = _find_typer_param_call(def_val, "Argument") if isinstance(def_val, ast.Call) else None
                from_annotation = False
                if arg_call is None:
                    arg_call = _find_typer_param_call(arg_node.annotation, "Argument")
                    from_annotation = arg_call is not None
                if arg_call is None:
                    opt_call = (
                        _find_typer_param_call(def_val, "Option") if isinstance(def_val, ast.Call) else None
                    ) or _find_typer_param_call(arg_node.annotation, "Option")
                    if opt_call is None and def_idx < 0:
                        pos_out.append((arg_node.arg, None, True))
                    continue
                call_has_default = bool(
                    (
                        arg_call.args
                        and not (isinstance(arg_call.args[0], ast.Constant) and arg_call.args[0].value is Ellipsis)
                    )
                    or any(
                        kw.arg == "default" and not (isinstance(kw.value, ast.Constant) and kw.value.value is Ellipsis)
                        for kw in arg_call.keywords
                    )
                )
                if from_annotation:
                    param_has_default = def_val is not None and not (
                        isinstance(def_val, ast.Constant) and def_val.value is Ellipsis
                    )
                    is_req = not (param_has_default or call_has_default)
                else:
                    is_req = not call_has_default
                metavar_val = next(
                    (
                        kw.value.value
                        for kw in arg_call.keywords
                        if kw.arg == "metavar"
                        and isinstance(kw.value, ast.Constant)
                        and isinstance(kw.value.value, str)
                    ),
                    None,
                )
                pos_out.append((arg_node.arg, metavar_val, is_req))
            return pos_out

        def _collect_commands_from_ast(
            scope_node: ast.AST,
            target_vars: set[str],
            full_module_node: ast.AST | None = None,
            local_imports: dict[str, tuple[str, str]] | None = None,
            local_mod_aliases: dict[str, str] | None = None,
            _visited: set[tuple[int, str]] | None = None,
        ) -> tuple[
            set[str],
            dict[str, set[str]],
            dict[str, list[tuple[str, str | None, bool]]],
            dict[tuple[str, ...], list[tuple[str, str | None, bool]]],
            dict[tuple[str, str], set[str]],
        ]:
            found: set[str] = set()
            sub_found: dict[str, set[str]] = {}
            found_pos: dict[str, list[tuple[str, str | None, bool]]] = {}
            sub_found_pos: dict[tuple[str, ...], list[tuple[str, str | None, bool]]] = {}
            nested_sub_found: dict[tuple[str, str], set[str]] = {}
            search_root = full_module_node or scope_node
            imp_map = local_imports if local_imports is not None else imported_symbol_modules
            mod_alias_map = local_mod_aliases if local_mod_aliases is not None else sibling_module_aliases
            visited = set(_visited) if _visited is not None else set()
            for tv in target_vars:
                visited.add((id(search_root), tv))
            _, scope_ctor_names = _collect_typer_vars(search_root)
            fn_defs_in_scope = {
                n.name: n for n in ast.walk(search_root) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            for node in ast.walk(scope_node):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in target_vars
                    and node.func.attr == "add_typer"
                ):
                    arg0 = node.args[0] if node.args else None
                    child_var = arg0.id if isinstance(arg0, ast.Name) else None
                    attr_target: tuple[str, str] | None = None
                    if isinstance(arg0, ast.Attribute) and isinstance(arg0.value, ast.Name):
                        attr_mod_ref = mod_alias_map.get(arg0.value.id, arg0.value.id)
                        attr_target = (attr_mod_ref, arg0.attr)
                    sub_name = _resolve_sub_typer_name(
                        node, child_var, scope_ctor_names, imp_map, attr_target=attr_target
                    )
                    child_cmds: set[str] = set()
                    grandchild_map: dict[str, set[str]] = {}
                    child_pos: dict[str, list[tuple[str, str | None, bool]]] = {}
                    grandchild_pos: dict[tuple[str, ...], list[tuple[str, str | None, bool]]] = {}
                    if child_var:
                        if (id(search_root), child_var) not in visited:
                            c_cmds, gc_map, c_pos, gc_pos, _ = _collect_commands_from_ast(
                                search_root,
                                {child_var},
                                search_root,
                                imp_map,
                                mod_alias_map,
                                _visited=visited,
                            )
                            child_cmds.update(c_cmds)
                            child_pos.update(c_pos)
                            grandchild_pos.update(gc_pos)
                            for gk, gv in gc_map.items():
                                grandchild_map.setdefault(gk, set()).update(gv)
                        if child_var in imp_map:
                            sib_mod, orig_var = imp_map[child_var]
                            sib_ast = _get_sibling_tree(sib_mod)
                            if sib_ast is not None and (id(sib_ast), orig_var) not in visited:
                                sib_mods, sib_imp = _scan_module_imports(sib_ast)
                                ext_cmds, ext_gc, ext_pos, ext_gc_pos, _ = _collect_commands_from_ast(
                                    sib_ast,
                                    {orig_var},
                                    sib_ast,
                                    sib_imp,
                                    sib_mods,
                                    _visited=visited,
                                )
                                child_cmds.update(ext_cmds)
                                child_pos.update(ext_pos)
                                grandchild_pos.update(ext_gc_pos)
                                for gk, gv in ext_gc.items():
                                    grandchild_map.setdefault(gk, set()).update(gv)
                    elif attr_target is not None:
                        sib_mod, orig_var = attr_target
                        sib_ast = _get_sibling_tree(sib_mod)
                        if sib_ast is not None and (id(sib_ast), orig_var) not in visited:
                            sib_mods, sib_imp = _scan_module_imports(sib_ast)
                            ext_cmds, ext_gc, ext_pos, ext_gc_pos, _ = _collect_commands_from_ast(
                                sib_ast,
                                {orig_var},
                                sib_ast,
                                sib_imp,
                                sib_mods,
                                _visited=visited,
                            )
                            child_cmds.update(ext_cmds)
                            child_pos.update(ext_pos)
                            grandchild_pos.update(ext_gc_pos)
                            for gk, gv in ext_gc.items():
                                grandchild_map.setdefault(gk, set()).update(gv)
                    if sub_name:
                        found.add(sub_name)
                        grp_set = sub_found.setdefault(sub_name, set())
                        grp_set.update(child_cmds)
                        for c_name, p_list in child_pos.items():
                            sub_found_pos[(sub_name, c_name)] = p_list
                        for gc_grp, gc_set in grandchild_map.items():
                            grp_set.update(gc_set)
                            nested_sub_found.setdefault((sub_name, gc_grp), set()).update(gc_set)
                        for gc_key, p_list in grandchild_pos.items():
                            sub_found_pos[(sub_name, *gc_key)] = p_list
                            if (sub_name, gc_key[-1]) not in sub_found_pos:
                                sub_found_pos[(sub_name, gc_key[-1])] = p_list
                    else:
                        # Unnamed add_typer(sub_app) mounts sub_app's commands at the parent level
                        found.update(child_cmds)
                        found_pos.update(child_pos)
                        for gk, gv in grandchild_map.items():
                            sub_found.setdefault(gk, set()).update(gv)
                        sub_found_pos.update(grandchild_pos)
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Call)
                    and isinstance(node.func.func, ast.Attribute)
                    and isinstance(node.func.func.value, ast.Name)
                    and node.func.func.value.id in target_vars
                    and node.func.func.attr == "command"
                ):
                    fn_arg_name = node.args[0].id if node.args and isinstance(node.args[0], ast.Name) else None
                    cmd_name = _extract_command_name_from_call(node.func, fn_arg_name)
                    if cmd_name:
                        found.add(cmd_name)
                        if fn_arg_name and fn_arg_name in fn_defs_in_scope:
                            found_pos[cmd_name] = _extract_typer_positional_args(fn_defs_in_scope[fn_arg_name])
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for dec in node.decorator_list:
                        if (
                            isinstance(dec, ast.Call)
                            and isinstance(dec.func, ast.Attribute)
                            and isinstance(dec.func.value, ast.Name)
                            and dec.func.value.id in target_vars
                            and dec.func.attr == "command"
                        ):
                            cmd_name = _extract_command_name_from_call(dec, node.name)
                            if cmd_name:
                                found.add(cmd_name)
                                found_pos[cmd_name] = _extract_typer_positional_args(node)
                        elif (
                            isinstance(dec, ast.Attribute)
                            and isinstance(dec.value, ast.Name)
                            and dec.value.id in target_vars
                            and dec.attr == "command"
                        ):
                            c_name = node.name.replace("_", "-")
                            found.add(c_name)
                            found_pos[c_name] = _extract_typer_positional_args(node)
            return found, sub_found, found_pos, sub_found_pos, nested_sub_found

        (
            commands,
            subcommands_by_group,
            cmd_pos_args,
            sub_pos_args,
            nested_subcommands_by_group,
        ) = _collect_commands_from_ast(tree, root_app_vars, tree)

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register"
                and isinstance(node.func.value, ast.Name)
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in root_app_vars
            ):
                mod_name = sibling_module_aliases.get(node.func.value.id, node.func.value.id)
                sib_tree = _get_sibling_tree(mod_name)
                if sib_tree is not None:
                    sib_mods_map, sib_imp_map = _scan_module_imports(sib_tree)
                    for sib_node in sib_tree.body:
                        if (
                            isinstance(sib_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and sib_node.name == "register"
                            and sib_node.args.args
                        ):
                            app_param = sib_node.args.args[0].arg
                            sib_cmds, sib_subs, sib_c_pos, sib_s_pos, sib_nested = _collect_commands_from_ast(
                                sib_node, {app_param}, sib_tree, sib_imp_map, sib_mods_map
                            )
                            commands.update(sib_cmds)
                            cmd_pos_args.update(sib_c_pos)
                            sub_pos_args.update(sib_s_pos)
                            for g_name, g_cmds in sib_subs.items():
                                subcommands_by_group.setdefault(g_name, set()).update(g_cmds)
                            for nk, nv in sib_nested.items():
                                nested_subcommands_by_group.setdefault(nk, set()).update(nv)
                            # Also check if sibling register() mounts subcommands onto an imported sub-typer (e.g. spec_app.add_typer(component_app, ...))
                            for sub_call in ast.walk(sib_node):
                                if (
                                    isinstance(sub_call, ast.Call)
                                    and isinstance(sub_call.func, ast.Attribute)
                                    and isinstance(sub_call.func.value, ast.Name)
                                    and sub_call.func.attr == "add_typer"
                                ):
                                    parent_var = sub_call.func.value.id
                                    parent_group = mounted_sub_apps.get(parent_var)
                                    if parent_group:
                                        extra_c, extra_sub, extra_c_pos, extra_s_pos, _ = _collect_commands_from_ast(
                                            sib_node, {parent_var}, sib_tree, sib_imp_map, sib_mods_map
                                        )
                                        grp_s = subcommands_by_group.setdefault(parent_group, set())
                                        grp_s.update(extra_c)
                                        for ec_name, ec_pos in extra_c_pos.items():
                                            sub_pos_args[(parent_group, ec_name)] = ec_pos
                                        for es_key, es_pos in extra_s_pos.items():
                                            sub_pos_args[(parent_group, *es_key)] = es_pos
                                            if (parent_group, es_key[-1]) not in sub_pos_args:
                                                sub_pos_args[(parent_group, es_key[-1])] = es_pos
                                        for gc_grp, gc_s in extra_sub.items():
                                            grp_s.update(gc_s)
                                            nested_subcommands_by_group.setdefault(
                                                (parent_group, gc_grp), set()
                                            ).update(gc_s)

    # Click CLI extraction (@click.group / @click.command / @<group>.group / @<group>.command / <group>.add_command)
    if not commands and (click_module_aliases or click_group_decorators or click_cmd_decorators):
        root_click_groups: set[str] = set()
        sub_click_groups: dict[str, str] = {}  # func_name -> group_command_name
        standalone_click_funcs: dict[str, str] = {}  # func_name -> default_command_name

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    dec_call = dec if isinstance(dec, ast.Call) else None
                    dec_target = dec_call.func if dec_call else dec
                    is_root_grp = (
                        isinstance(dec_target, ast.Attribute)
                        and isinstance(dec_target.value, ast.Name)
                        and dec_target.value.id in click_module_aliases
                        and dec_target.attr == "group"
                    ) or (isinstance(dec_target, ast.Name) and dec_target.id in click_group_decorators)
                    if is_root_grp:
                        root_click_groups.add(node.name)
                        grp_cmd = (
                            _extract_command_name_from_call(dec_call, node.name)
                            if dec_call
                            else node.name.replace("_", "-")
                        )
                        if grp_cmd:
                            standalone_click_funcs[node.name] = grp_cmd

        # Iteratively discover nested @<group>.group() definitions and <group>.add_command(<sub_group>) mounts
        changed = True
        while changed:
            changed = False
            known_groups = root_click_groups | set(sub_click_groups)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in known_groups:
                    for dec in node.decorator_list:
                        dec_call = dec if isinstance(dec, ast.Call) else None
                        dec_target = dec_call.func if dec_call else dec
                        if (
                            isinstance(dec_target, ast.Attribute)
                            and isinstance(dec_target.value, ast.Name)
                            and dec_target.value.id in known_groups
                            and dec_target.attr == "group"
                        ):
                            grp_cmd = (
                                _extract_command_name_from_call(dec_call, node.name)
                                if dec_call
                                else node.name.replace("_", "-")
                            )
                            if grp_cmd:
                                sub_click_groups[node.name] = grp_cmd
                                parent_grp = dec_target.value.id
                                if parent_grp in sub_click_groups:
                                    subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(grp_cmd)
                                else:
                                    commands.add(grp_cmd)
                                changed = True
                elif (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in known_groups
                    and node.func.attr == "add_command"
                    and node.args
                    and isinstance(node.args[0], ast.Name)
                    and node.args[0].id in root_click_groups
                ):
                    parent_grp = node.func.value.id
                    target_var = node.args[0].id
                    explicit_name = next(
                        (
                            kw.value.value
                            for kw in node.keywords
                            if kw.arg == "name"
                            and isinstance(kw.value, ast.Constant)
                            and isinstance(kw.value.value, str)
                        ),
                        None,
                    )
                    if (
                        not explicit_name
                        and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant)
                        and isinstance(node.args[1].value, str)
                    ):
                        explicit_name = node.args[1].value
                    grp_cmd = explicit_name or standalone_click_funcs.get(target_var) or target_var.replace("_", "-")
                    root_click_groups.discard(target_var)
                    sub_click_groups[target_var] = grp_cmd
                    if parent_grp in sub_click_groups:
                        subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(grp_cmd)
                    else:
                        commands.add(grp_cmd)
                    changed = True

        all_click_groups = root_click_groups | set(sub_click_groups)

        # Collect @click.command / @<group>.command functions
        decorated_Group_cmds: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    dec_call = dec if isinstance(dec, ast.Call) else None
                    dec_target = dec_call.func if dec_call else dec
                    is_Group_cmd = (
                        isinstance(dec_target, ast.Attribute)
                        and isinstance(dec_target.value, ast.Name)
                        and dec_target.value.id in all_click_groups
                        and dec_target.attr == "command"
                    )
                    is_standalone_cmd = (
                        isinstance(dec_target, ast.Attribute)
                        and isinstance(dec_target.value, ast.Name)
                        and dec_target.value.id in click_module_aliases
                        and dec_target.attr == "command"
                    ) or (isinstance(dec_target, ast.Name) and dec_target.id in click_cmd_decorators)
                    if is_Group_cmd or (is_standalone_cmd and node.name not in all_click_groups):
                        cmd_name = (
                            _extract_command_name_from_call(dec_call, node.name)
                            if dec_call
                            else node.name.replace("_", "-")
                        )
                        if cmd_name:
                            standalone_click_funcs[node.name] = cmd_name
                            if is_Group_cmd:
                                decorated_Group_cmds.add(node.name)
                                parent_grp = dec_target.value.id  # type: ignore[union-attr]
                                if parent_grp in sub_click_groups:
                                    subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(cmd_name)
                                else:
                                    commands.add(cmd_name)

        # Inspect <group>.add_command(cmd_obj, name=...) calls
        mounted_via_add_cmd: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in all_click_groups
                and node.func.attr == "add_command"
                and node.args
            ):
                parent_grp = node.func.value.id
                explicit_name = next(
                    (
                        kw.value.value
                        for kw in node.keywords
                        if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)
                    ),
                    None,
                )
                if (
                    not explicit_name
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                ):
                    explicit_name = node.args[1].value
                first_arg = node.args[0]
                if isinstance(first_arg, ast.Name):
                    target_var = first_arg.id
                    mounted_via_add_cmd.add(target_var)
                    resolved_name = (
                        explicit_name or standalone_click_funcs.get(target_var) or target_var.replace("_", "-")
                    )
                    if resolved_name:
                        if target_var in root_click_groups:
                            root_click_groups.discard(target_var)
                            sub_click_groups[target_var] = resolved_name
                        if parent_grp in sub_click_groups:
                            subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(resolved_name)
                        else:
                            commands.add(resolved_name)
                elif explicit_name:
                    if parent_grp in sub_click_groups:
                        subcommands_by_group.setdefault(sub_click_groups[parent_grp], set()).add(explicit_name)
                    else:
                        commands.add(explicit_name)

        # Include standalone @click.command functions when not mounted via add_command
        for fn_name, cmd_name in standalone_click_funcs.items():
            if (
                fn_name not in all_click_groups
                and fn_name not in decorated_Group_cmds
                and fn_name not in mounted_via_add_cmd
            ):
                commands.add(cmd_name)

    # argparse CLI extraction (subparsers.add_parser("cmd"))
    if not commands and has_argparse:
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_parser"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                commands.add(node.args[0].value)

    if not commands:
        return

    clean_body = strip_asciidoc_comments(body)

    # 1. Forward verification (Code -> Spec): every discovered top-level CLI command must be documented
    for cmd_name in sorted(commands):
        if not re.search(rf"(?:`|\b){re.escape(cmd_name)}\b", clean_body):
            report.broken_references.append(
                f"Contract drift in {doc.file_path.name}: CLI command '{cmd_name}' defined in '{cli_file.name}' is not documented in contract '{doc.doc_id}'"
            )

    def _parse_doc_positional_placeholders(sig_expr: str, cmd_token: str) -> list[tuple[str, bool]]:
        """Extract (placeholder_name, is_required) after cmd_token, stripping [--opt ...] and --opt <val>."""
        cleaned = re.sub(r"\[-[^\]]*\]", " ", sig_expr)
        toks = cleaned.strip().split()
        if cmd_token in toks:
            toks = toks[toks.index(cmd_token) + 1 :]
        out: list[tuple[str, bool]] = []
        idx_t = 0
        while idx_t < len(toks):
            t = toks[idx_t]
            if t.startswith("-"):
                if (
                    idx_t + 1 < len(toks)
                    and not toks[idx_t + 1].startswith("-")
                    and (toks[idx_t + 1].startswith("<") or toks[idx_t + 1].isupper())
                ):
                    idx_t += 2
                    continue
                idx_t += 1
                continue
            t_clean = t.rstrip(".")
            if t_clean.startswith("<") and t_clean.endswith(">") and len(t_clean) > 2:
                inner = t_clean[1:-1].rstrip(".").strip()
                if inner:
                    out.append((inner, True))
            elif (
                t_clean.startswith("[")
                and t_clean.endswith("]")
                and len(t_clean) > 2
                and not t_clean[1:-1].startswith("-")
            ):
                inner = t_clean[1:-1].rstrip(".").strip()
                if inner:
                    out.append((inner, False))
            idx_t += 1
        return out

    def _verify_positional_parity(
        cmd_label: str,
        doc_pos: list[tuple[str, bool]],
        code_pos: list[tuple[str, str | None, bool]],
    ) -> None:
        if not doc_pos and not code_pos:
            return
        doc_req_cnt = sum(1 for _, r in doc_pos if r)
        code_req_cnt = sum(1 for _, _, r in code_pos if r)
        if len(doc_pos) != len(code_pos) or doc_req_cnt != code_req_cnt:
            report.broken_references.append(
                f"Contract drift in {doc.file_path.name}: CLI command '{cmd_label}' in contract '{doc.doc_id}' documents "
                f"{len(doc_pos)} positional argument(s) ({doc_req_cnt} required) but '{cli_file.name}' defines {len(code_pos)} ({code_req_cnt} required)"
            )
            return
        for (doc_ph, _), (param_name, metavar, _) in zip(doc_pos, code_pos, strict=False):
            doc_norm = doc_ph.lower().replace("-", "_").strip()
            param_norm = param_name.lower().replace("-", "_").strip()
            meta_norm = (metavar or "").lower().replace("-", "_").strip()
            doc_parts = {p for p in doc_norm.split("_") if p and p not in ("or", "and")}
            param_parts = {p for p in param_norm.split("_") if p and p not in ("or", "and")}
            meta_parts = {p for p in meta_norm.split("_") if p and p not in ("or", "and")}
            aligned = (
                doc_norm in (param_norm, meta_norm)
                or bool(doc_parts & (param_parts | meta_parts))
                or (param_norm == "target" and doc_norm in ("id_or_path", "target"))
            )
            if not aligned:
                report.broken_references.append(
                    f"Contract drift in {doc.file_path.name}: CLI command '{cmd_label}' positional placeholder '{doc_ph}' "
                    f"in contract '{doc.doc_id}' does not align with parameter '{param_name}' in '{cli_file.name}'"
                )

    # 2. Reverse verification (Spec -> Code) & Subcommand verification (DLT-2026-082, DLT-2026-114)
    heading_matches = list(re.finditer(r"^===\s+`([^`]+)`(?:\s*/\s*`([^`]+)`)?", clean_body, re.MULTILINE))
    for idx, hm in enumerate(heading_matches):
        sec_start = hm.end()
        sec_end = heading_matches[idx + 1].start() if idx + 1 < len(heading_matches) else len(clean_body)
        next_h2 = re.search(r"^==\s+", clean_body[sec_start:sec_end], re.MULTILINE)
        if next_h2:
            sec_end = sec_start + next_h2.start()
        sec_body = clean_body[sec_start:sec_end]

        candidates = [hm.group(1)]
        if hm.group(2):
            candidates.append(hm.group(2))
        resolved_cmd: str | None = None
        matched_cand_expr = candidates[0]
        for cand_expr in candidates:
            tokens = cand_expr.strip().split()
            if not tokens:
                continue
            tok = tokens[0]
            if len(tokens) >= 2 and tok not in commands and not tokens[1].startswith(("[", "<", "{")):
                tok = tokens[1]
            if tok.startswith(("-", "[", "<", "{")) or any(ch in tok for ch in ("*", "|", ".", "/")):
                continue
            resolved_cmd = tok
            matched_cand_expr = cand_expr
            if tok in commands:
                break

        if resolved_cmd and resolved_cmd not in commands:
            report.broken_references.append(
                f"Contract drift in {doc.file_path.name}: documented CLI command '{resolved_cmd}' in contract '{doc.doc_id}' does not exist in '{cli_file.name}'"
            )
        elif resolved_cmd and resolved_cmd in cmd_pos_args and resolved_cmd not in subcommands_by_group:
            doc_pos_top = _parse_doc_positional_placeholders(matched_cand_expr, resolved_cmd)
            _verify_positional_parity(resolved_cmd, doc_pos_top, cmd_pos_args[resolved_cmd])
        elif resolved_cmd and resolved_cmd in subcommands_by_group and "Sub-commands" in sec_body:
            expected_subs = subcommands_by_group[resolved_cmd]
            for sub_cmd in sorted(expected_subs):
                if not re.search(rf"(?:`|\b){re.escape(sub_cmd)}\b", sec_body):
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: CLI subcommand '{resolved_cmd} {sub_cmd}' is not documented under '{resolved_cmd}' in contract '{doc.doc_id}'"
                    )
            sub_sec_m = re.search(
                r"\*\s+\*\*Sub-commands\*\*\s*:(.*?)(?=^\*\s+\*\*|^={2,}\s+|\Z)",
                sec_body,
                re.MULTILINE | re.DOTALL,
            )
            sub_bullets_text = sub_sec_m.group(1) if sub_sec_m else sec_body
            for bullet_m in re.finditer(
                r"^\s*-\s+`([^`]+)`",
                sub_bullets_text,
                re.MULTILINE,
            ):
                raw_bullet_sig = bullet_m.group(1).strip()
                bullet_tokens = [
                    t
                    for t in raw_bullet_sig.split()
                    if not t.startswith(("-", "[", "<", "{")) and not any(ch in t for ch in ("*", "|", ".", "/"))
                ]
                if not bullet_tokens:
                    continue
                if resolved_cmd in bullet_tokens:
                    rc_idx = bullet_tokens.index(resolved_cmd)
                    if rc_idx + 1 < len(bullet_tokens):
                        doc_sub = bullet_tokens[rc_idx + 1]
                    else:
                        continue
                else:
                    rc_idx = -1
                    doc_sub = bullet_tokens[0]
                if not re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", doc_sub):
                    continue
                nested_leaf: str | None = None
                if (
                    (resolved_cmd, doc_sub) in nested_subcommands_by_group
                    and rc_idx >= 0
                    and rc_idx + 2 < len(bullet_tokens)
                    and re.match(r"^[a-zA-Z][a-zA-Z0-9_-]*$", bullet_tokens[rc_idx + 2])
                ):
                    nested_leaf = bullet_tokens[rc_idx + 2]
                if doc_sub not in expected_subs:
                    report.broken_references.append(
                        f"Contract drift in {doc.file_path.name}: documented CLI subcommand '{resolved_cmd} {doc_sub}' in contract '{doc.doc_id}' does not exist in '{cli_file.name}'"
                    )
                elif nested_leaf is not None:
                    if nested_leaf not in nested_subcommands_by_group[(resolved_cmd, doc_sub)]:
                        report.broken_references.append(
                            f"Contract drift in {doc.file_path.name}: documented CLI subcommand '{resolved_cmd} {doc_sub} {nested_leaf}' in contract '{doc.doc_id}' does not exist in '{cli_file.name}'"
                        )
                    elif (resolved_cmd, doc_sub, nested_leaf) in sub_pos_args:
                        doc_pos_nested = _parse_doc_positional_placeholders(raw_bullet_sig, nested_leaf)
                        _verify_positional_parity(
                            f"{resolved_cmd} {doc_sub} {nested_leaf}",
                            doc_pos_nested,
                            sub_pos_args[(resolved_cmd, doc_sub, nested_leaf)],
                        )
                elif (resolved_cmd, doc_sub) in sub_pos_args:
                    doc_pos_sub = _parse_doc_positional_placeholders(raw_bullet_sig, doc_sub)
                    _verify_positional_parity(
                        f"{resolved_cmd} {doc_sub}",
                        doc_pos_sub,
                        sub_pos_args[(resolved_cmd, doc_sub)],
                    )
