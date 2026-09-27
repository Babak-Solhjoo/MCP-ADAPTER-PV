"""Altium Designer adapter: runs DelphiScript/VBScript script projects through X2.EXE.

Altium is driven by its Scripting System. The adapter can
* run an existing script project (``-RScriptFile:<.PrjScr> -RProcName:<Unit>Proc>``),
* wrap ad-hoc DelphiScript into a generated script project and run it,
* open a project/document in the GUI.
The command-line switches follow Altium's documented "run script from command line" form; set
``ALTIUM_SCRIPT_ARGS`` to override the switch template if your release differs, e.g.
``-RScriptFile:{script} -RProcName:{proc}``.
"""
from __future__ import annotations

import re

from ..config import env
from .base import BaseAdapter, RunResult, resolve_path

DEFAULT_SWITCHES = "-RScriptFile:{script} -RProcName:{proc}"


class AltiumAdapter(BaseAdapter):
    id = "altium"
    name = "Altium Designer"
    env_var = "ALTIUM_EXE"
    exe_names = ["X2.EXE", "X2.exe"]
    exe_patterns = ["Altium/AD*/X2.EXE", "Altium/AD*/X2.exe", "Altium/*/X2.EXE"]
    install_hint = "Install Altium Designer and set ALTIUM_EXE to X2.EXE."

    def open(self, path: str) -> RunResult:
        """Open a project or document in Altium Designer (detached)."""
        if not self.is_available():
            return self.unavailable()
        p = resolve_path(path)
        return self.launch_detached([self.executable() or "X2.EXE", str(p)], cwd=str(p.parent))

    def run_script_project(self, script_project: str, procedure: str, timeout: int | None = None) -> RunResult:
        """Run *procedure* ("UnitName>ProcName") from an existing .PrjScr script project."""
        if not self.is_available():
            return self.unavailable()
        proj = resolve_path(script_project)
        if not proj.exists():
            return RunResult(ok=False, software=self.id, command="", error=f"File not found: {proj}")
        template = env("ALTIUM_SCRIPT_ARGS") or DEFAULT_SWITCHES
        switches = [s.format(script=str(proj), proc=procedure) for s in template.split()]
        return self.run_command([self.executable() or "X2.EXE", *switches], cwd=proj.parent, timeout=timeout,
                                artifacts={"script_project": str(proj), "procedure": procedure})

    def run_delphiscript(self, code: str, procedure: str = "Main", unit_name: str = "MCPScript",
                         timeout: int | None = None) -> RunResult:
        """Wrap DelphiScript *code* in a script unit + project and run it.

        *code* must define a procedure named *procedure* (e.g. ``procedure Main; begin ... end;``).
        Use ShowMessage/ShowInfo or write files from the script to report results.
        """
        unit = re.sub(r"[^A-Za-z0-9_]", "_", unit_name)
        folder = self.scripts_dir() / f"{unit}_{procedure}"
        folder.mkdir(parents=True, exist_ok=True)
        pas = folder / f"{unit}.pas"
        pas.write_text(code, encoding="utf-8")
        prj = folder / f"{unit}.PrjScr"
        prj.write_text(
            "[Design]\nVersion=1.0\n\n"
            f"[Document1]\nDocumentPath={pas.name}\n",
            encoding="utf-8",
        )
        res = self.run_script_project(str(prj), f"{unit}>{procedure}", timeout=timeout)
        res.artifacts.update({"script_unit": str(pas), "script_project": str(prj)})
        return res

    # ---- ready-made DelphiScript generators -----------------------------------------------------
    @staticmethod
    def script_export_bom(project_path: str, output_csv: str) -> str:
        """DelphiScript that opens a project and writes a simple component BOM (CSV) from the first SchDoc set."""
        return f"""
procedure Main;
var
    Workspace : IWorkspace;
    Project   : IProject;
    Doc       : IDocument;
    SchDoc    : ISch_Document;
    Iterator  : ISch_Iterator;
    Comp      : ISch_Component;
    Lines     : TStringList;
    i         : Integer;
begin
    Workspace := GetWorkspace;
    if Workspace = nil then exit;
    Project := Workspace.DM_OpenProject('{str(resolve_path(project_path))}', True);
    if Project = nil then exit;
    Lines := TStringList.Create;
    Lines.Add('Designator,Comment,Footprint,Document');
    for i := 0 to Project.DM_LogicalDocumentCount - 1 do
    begin
        Doc := Project.DM_LogicalDocuments(i);
        if Doc.DM_DocumentKind = 'SCH' then
        begin
            Client.OpenDocument('SCH', Doc.DM_FullPath);
            SchDoc := SchServer.GetSchDocumentByPath(Doc.DM_FullPath);
            if SchDoc = nil then continue;
            Iterator := SchDoc.SchIterator_Create;
            Iterator.AddFilter_ObjectSet(MkSet(eSchComponent));
            Comp := Iterator.FirstSchObject;
            while Comp <> nil do
            begin
                Lines.Add(Comp.Designator.Text + ',' + Comp.Comment.Text + ',' + Comp.CurrentPartID + ',' + Doc.DM_FileName);
                Comp := Iterator.NextSchObject;
            end;
            SchDoc.SchIterator_Destroy(Iterator);
        end;
    end;
    Lines.SaveToFile('{str(resolve_path(output_csv))}');
    Lines.Free;
end;
"""

    @staticmethod
    def script_pcb_stats(output_txt: str) -> str:
        """DelphiScript that reports component/track/via counts of the current PCB document."""
        return f"""
procedure Main;
var
    Board    : IPCB_Board;
    Iterator : IPCB_BoardIterator;
    Prim     : IPCB_Primitive;
    Comps, Tracks, Vias, Pads : Integer;
    Lines    : TStringList;
begin
    Board := PCBServer.GetCurrentPCBBoard;
    if Board = nil then exit;
    Comps := 0; Tracks := 0; Vias := 0; Pads := 0;
    Iterator := Board.BoardIterator_Create;
    Iterator.AddFilter_ObjectSet(MkSet(eComponentObject, eTrackObject, eViaObject, ePadObject));
    Iterator.AddFilter_LayerSet(AllLayers);
    Iterator.AddFilter_Method(eProcessAll);
    Prim := Iterator.FirstPCBObject;
    while Prim <> nil do
    begin
        case Prim.ObjectId of
            eComponentObject : Inc(Comps);
            eTrackObject     : Inc(Tracks);
            eViaObject       : Inc(Vias);
            ePadObject       : Inc(Pads);
        end;
        Prim := Iterator.NextPCBObject;
    end;
    Board.BoardIterator_Destroy(Iterator);
    Lines := TStringList.Create;
    Lines.Add('board=' + Board.FileName);
    Lines.Add('components=' + IntToStr(Comps));
    Lines.Add('tracks=' + IntToStr(Tracks));
    Lines.Add('vias=' + IntToStr(Vias));
    Lines.Add('pads=' + IntToStr(Pads));
    Lines.SaveToFile('{str(resolve_path(output_txt))}');
    Lines.Free;
end;
"""


altium_adapter = AltiumAdapter()
