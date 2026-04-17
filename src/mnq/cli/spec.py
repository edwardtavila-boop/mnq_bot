"""[REAL] `mnq spec ...` CLI — render specs to generator targets.

Subcommands:
    mnq spec render <path> --target pine|python [--out DIR]
        Generate and write a target artifact.

    mnq spec rehash <path>
        Recompute and stamp the content_hash on the spec file.

    mnq spec approve <spec-path> --manifest <manifest-yaml> \
                     --approved-by <name> --gauntlet-run-id <id> \
                     [--notes "..."]
        Approve a spec and add it to the manifest.

    mnq spec verify <spec-path> --manifest <manifest-yaml>
        Verify that a spec is approved.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from mnq.spec.loader import load_spec

app = typer.Typer(help="Strategy spec operations.", no_args_is_help=True)
console = Console()


@app.command("render")
def render(
    spec_path: Annotated[Path, typer.Argument(exists=True, readable=True, help="Path to spec YAML.")],
    target: Annotated[str, typer.Option("--target", "-t", help="pine|python")],
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output directory (default auto).")] = None,
) -> None:
    """Render a spec to a generator target and write to disk."""
    spec = load_spec(spec_path)
    target_lc = target.lower()

    if target_lc == "pine":
        from mnq.generators.pine import render_pine

        src = render_pine(spec)
        out_dir = out or (Path.cwd() / "specs" / "generated_pine")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{spec.strategy.id}.pine"
        out_file.write_text(src)
        console.print(f"[green]wrote[/green] {out_file} ({len(src)} bytes)")
        return

    if target_lc == "python":
        from mnq.generators.python_exec import render_python

        src = render_python(spec)
        out_dir = out or (Path.cwd() / "specs" / "generated_python")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"{spec.strategy.id}.py"
        out_file.write_text(src)
        console.print(f"[green]wrote[/green] {out_file} ({len(src)} bytes)")
        return

    console.print(f"[red]unknown target:[/red] {target!r} (expected 'pine' or 'python')")
    raise typer.Exit(code=2)


@app.command("rehash")
def rehash(
    spec_path: Annotated[Path, typer.Argument(exists=True, readable=True)],
) -> None:
    """Recompute and stamp `strategy.content_hash` on the spec file."""
    import yaml

    from mnq.spec.hash import hash_spec, stamp_hash
    from mnq.spec.loader import dump_spec

    with spec_path.open() as f:
        raw = yaml.safe_load(f)
    from mnq.spec.schema import StrategySpec

    spec = StrategySpec.model_validate(raw)
    stamped = stamp_hash(spec)
    dump_spec(stamped, spec_path)
    console.print(f"[green]stamped[/green] {spec_path} -> {hash_spec(stamped)}")


@app.command("approve")
def approve(
    spec_path: Annotated[Path, typer.Argument(exists=True, readable=True,
                                              help="Path to spec YAML.")],
    manifest: Annotated[Path, typer.Option("--manifest", "-m",
                                           help="Path to approval manifest YAML.")],
    approved_by: Annotated[str, typer.Option("--approved-by", "-a",
                                             help="Approver name or ticket ID.")],
    gauntlet_run_id: Annotated[str, typer.Option("--gauntlet-run-id", "-g",
                                                 help="Gauntlet run identifier.")],
    notes: Annotated[str, typer.Option("--notes", "-n",
                                       help="Optional approval notes.")] = "",
) -> None:
    """Approve a spec and add it to the manifest.

    Verifies the spec's stamped hash matches the computed hash, then
    appends the approval entry to the manifest file.
    """
    from mnq.spec.hash import hash_spec
    from mnq.spec.manifest import ApprovalManifest

    # Load spec
    spec = load_spec(spec_path)
    stamped_hash = spec.strategy.content_hash
    computed_hash = hash_spec(spec)

    # Verify hash match
    if stamped_hash != computed_hash:
        console.print(
            f"[red]error:[/red] spec hash mismatch: stamped {stamped_hash} != "
            f"computed {computed_hash}. Run 'mnq spec rehash' first."
        )
        raise typer.Exit(code=1)

    # Load or create manifest
    mgmt = (
        ApprovalManifest.load(manifest)
        if manifest.exists()
        else ApprovalManifest(specs=())
    )

    # Check if already approved
    if mgmt.find(computed_hash) is not None:
        console.print(
            f"[yellow]note:[/yellow] spec {computed_hash} is already approved."
        )
        raise typer.Exit(code=0)

    # Approve
    mgmt = mgmt.approve(
        spec_id=spec.strategy.id,
        content_hash=computed_hash,
        approved_by=approved_by,
        gauntlet_run_id=gauntlet_run_id,
        notes=notes,
    )
    mgmt.save(manifest)

    console.print(
        f"[green]approved[/green] spec {spec.strategy.id} "
        f"({computed_hash[:16]}...) in {manifest}"
    )


@app.command("verify")
def verify(
    spec_path: Annotated[Path, typer.Argument(exists=True, readable=True,
                                              help="Path to spec YAML.")],
    manifest: Annotated[Path, typer.Option("--manifest", "-m",
                                           help="Path to approval manifest YAML.")],
) -> None:
    """Verify that a spec is approved.

    Exits 0 if approved, 1 if not.
    """
    from mnq.spec.manifest import ApprovalManifest, UnapprovedSpecError

    # Load spec
    spec = load_spec(spec_path)

    # Load manifest
    if not manifest.exists():
        console.print(
            f"[red]error:[/red] manifest not found: {manifest}"
        )
        raise typer.Exit(code=1)

    mgmt = ApprovalManifest.load(manifest)

    # Verify
    try:
        from mnq.spec.manifest import require_approved
        approved = require_approved(spec, mgmt)
        console.print(
            f"[green]ok[/green] spec {spec.strategy.id} is approved "
            f"by {approved.approved_by} in {approved.gauntlet_run_id}"
        )
        raise typer.Exit(code=0)
    except UnapprovedSpecError as e:
        console.print(f"[red]unapproved:[/red] {e}")
        raise typer.Exit(code=1) from e
