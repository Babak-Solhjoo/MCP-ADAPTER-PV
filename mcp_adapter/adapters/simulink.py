"""Simulink adapter: drives models through the MATLAB command line (``matlab -batch``).

Every action generates MATLAB code that uses the Simulink programmatic API
(load_system, Simulink.SimulationInput, sim, find_system, get_param, set_param).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import RunResult, resolve_path
from .matlab import MatlabAdapter, matlab_string, to_matlab_literal


def _model_prelude(model: str) -> tuple[str, str]:
    """Return (model_name, MATLAB code that loads the model) for a name or a .slx/.mdl path."""
    p = Path(model)
    if p.suffix.lower() in (".slx", ".mdl"):
        rp = resolve_path(model)
        return rp.stem, (
            f"cd({matlab_string(str(rp.parent))});\n"
            f"mdl = {matlab_string(rp.stem)};\nload_system(mdl);\n"
        )
    return model, f"mdl = {matlab_string(model)};\nload_system(mdl);\n"


class SimulinkAdapter(MatlabAdapter):
    id = "simulink"
    name = "Simulink"
    install_hint = "Simulink runs through MATLAB; set MATLAB_EXE if matlab is not on PATH."

    def simulate(self, model: str, stop_time: float | str | None = None, variables: dict[str, Any] | None = None,
                 block_parameters: list[dict[str, str]] | None = None, save_mat: bool = True,
                 max_samples: int = 200, timeout: int | None = None) -> RunResult:
        """Simulate *model* headless and return a summary of logged signals/outputs.

        variables:        workspace variables the model references, e.g. {"K": 2.5}
        block_parameters: [{"block": "model/Gain", "parameter": "Gain", "value": "10"}, ...]
        The full results are saved to a .mat file in the outputs folder when save_mat is true.
        """
        name, prelude = _model_prelude(model)
        mat_path = self.scripts_dir() / f"{name}_simout.mat"
        code = [prelude, "in = Simulink.SimulationInput(mdl);"]
        if stop_time is not None:
            code.append(f"in = in.setModelParameter('StopTime', {matlab_string(str(stop_time))});")
        for k, v in (variables or {}).items():
            code.append(f"in = in.setVariable({matlab_string(k)}, {to_matlab_literal(v)});")
        for bp in block_parameters or []:
            code.append(
                f"in = in.setBlockParameter({matlab_string(bp['block'])}, {matlab_string(bp['parameter'])}, "
                f"{matlab_string(str(bp['value']))});"
            )
        code.append(
            f"""
out = sim(in);
summary = struct();
summary.model = mdl;
summary.stopTime = get_param(mdl, 'StopTime');
summary.solver = get_param(mdl, 'Solver');
summary.signals = {{}};
maxN = {int(max_samples)};
if isprop(out, 'tout') || any(strcmp(out.who, 'tout'))
    t = out.tout; summary.tout_samples = numel(t);
    if ~isempty(t), summary.t_start = t(1); summary.t_end = t(end); end
end
names = out.who;
for i = 1:numel(names)
    nm = names{{i}};
    val = out.get(nm);
    if isa(val, 'Simulink.SimulationData.Dataset')
        for j = 1:val.numElements
            el = val.getElement(j);
            s = struct('dataset', nm, 'name', el.Name);
            try
                ts = el.Values;
                if isa(ts, 'timeseries')
                    d = ts.Data; tt = ts.Time;
                    s.samples = numel(tt);
                    idx = unique(round(linspace(1, numel(tt), min(numel(tt), maxN))));
                    s.time = tt(idx);
                    s.data = d(idx, :);
                    s.min = min(d(:)); s.max = max(d(:)); s.final = d(end, :);
                end
            catch
            end
            summary.signals{{end+1}} = s;
        end
    elseif isa(val, 'timeseries')
        d = val.Data; tt = val.Time;
        idx = unique(round(linspace(1, numel(tt), min(numel(tt), maxN))));
        summary.signals{{end+1}} = struct('name', nm, 'samples', numel(tt), 'time', tt(idx), 'data', d(idx, :), 'min', min(d(:)), 'max', max(d(:)), 'final', d(end, :));
    elseif isnumeric(val) && ~isempty(val) && ~strcmp(nm, 'tout')
        idx = unique(round(linspace(1, size(val,1), min(size(val,1), maxN))));
        summary.signals{{end+1}} = struct('name', nm, 'samples', size(val,1), 'data', val(idx, :));
    end
end
"""
        )
        if save_mat:
            code.append(f"save({matlab_string(str(mat_path))}, 'out'); summary.mat_file = {matlab_string(str(mat_path))};")
        res = self.run_code("\n".join(code), capture=["summary"], timeout=timeout)
        if save_mat:
            res.artifacts["mat_file"] = str(mat_path)
        return res

    def list_blocks(self, model: str, block_type: str | None = None, timeout: int | None = None) -> RunResult:
        name, prelude = _model_prelude(model)
        filt = f", 'BlockType', {matlab_string(block_type)}" if block_type else ""
        code = (
            prelude
            + f"paths = find_system(mdl, 'Type', 'block'{filt});\n"
            "types = get_param(paths, 'BlockType');\n"
            "if ischar(types), types = {types}; end\n"
            "blocks = struct('path', paths, 'type', types);\n"
            "count = numel(paths);"
        )
        return self.run_code(code, capture=["blocks", "count"], timeout=timeout)

    def model_info(self, model: str, timeout: int | None = None) -> RunResult:
        name, prelude = _model_prelude(model)
        code = (
            prelude
            + """
info = struct();
info.name = mdl;
info.file = get_param(mdl, 'FileName');
info.solver = get_param(mdl, 'Solver');
info.solverType = get_param(mdl, 'SolverType');
info.startTime = get_param(mdl, 'StartTime');
info.stopTime = get_param(mdl, 'StopTime');
info.fixedStep = get_param(mdl, 'FixedStep');
info.blockCount = numel(find_system(mdl, 'Type', 'block'));
info.inports = find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Inport');
info.outports = find_system(mdl, 'SearchDepth', 1, 'BlockType', 'Outport');
info.subsystems = find_system(mdl, 'BlockType', 'SubSystem');
info.version = get_param(mdl, 'Version');
"""
        )
        return self.run_code(code, capture=["info"], timeout=timeout)

    def get_block_parameters(self, block_path: str, parameters: list[str] | None = None,
                             timeout: int | None = None) -> RunResult:
        model = block_path.split("/")[0]
        _, prelude = _model_prelude(model)
        if parameters:
            items = ", ".join(
                f"{matlab_string(p)}, get_param({matlab_string(block_path)}, {matlab_string(p)})" for p in parameters
            )
            code = prelude + f"values = struct({items});"
        else:
            code = prelude + f"values = get_param({matlab_string(block_path)}, 'DialogParameters');"
        return self.run_code(code, capture=["values"], timeout=timeout)

    def set_block_parameters(self, block_path: str, parameters: dict[str, str], save: bool = True,
                             timeout: int | None = None) -> RunResult:
        model = block_path.split("/")[0]
        _, prelude = _model_prelude(model)
        sets = "\n".join(
            f"set_param({matlab_string(block_path)}, {matlab_string(k)}, {matlab_string(str(v))});"
            for k, v in parameters.items()
        )
        code = prelude + sets + ("\nsave_system(mdl);" if save else "") + "\nsaved = true;"
        return self.run_code(code, capture=["saved"], timeout=timeout)

    def build_model(self, model_name: str, blocks: list[dict[str, str]], lines: list[dict[str, str]],
                    directory: str | None = None, timeout: int | None = None) -> RunResult:
        """Create a new model programmatically.

        blocks: [{"library": "simulink/Sources/Sine Wave", "name": "Src", "position": "[100 100 130 130]",
                  "parameters": {"Frequency": "2"}}]
        lines:  [{"from": "Src/1", "to": "Scope/1"}]
        """
        target_dir = resolve_path(directory) if directory else self.scripts_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        code = [f"cd({matlab_string(str(target_dir))});", f"mdl = {matlab_string(model_name)};",
                "if bdIsLoaded(mdl), close_system(mdl, 0); end", "new_system(mdl); open_system(mdl);"]
        for b in blocks:
            dest = f"[mdl {matlab_string('/' + str(b['name']))}]"
            pos = b.get("position")
            extra = ""
            if pos:
                if not (isinstance(pos, (list, tuple)) and len(pos) == 4
                        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pos)):
                    return RunResult(ok=False, software=self.id, command="",
                                     error=f"block {b['name']!r}: position must be four numbers [left top right bottom]")
                extra = ", 'Position', [" + " ".join(repr(float(v)) for v in pos) + "]"
            code.append(f"add_block({matlab_string(b['library'])}, {dest}{extra});")
            for k, v in (b.get("parameters") or {}).items():
                code.append(f"set_param({dest}, {matlab_string(k)}, {matlab_string(str(v))});")
        for ln in lines:
            code.append(
                f"add_line(mdl, {matlab_string(ln['from'])}, {matlab_string(ln['to'])}, 'autorouting', 'on');"
            )
        code.append("save_system(mdl); file = get_param(mdl, 'FileName'); close_system(mdl);")
        return self.run_code("\n".join(code), capture=["file"], timeout=timeout)

    def export_diagram(self, model: str, output_file: str, timeout: int | None = None) -> RunResult:
        """Print the block diagram to PNG/PDF/SVG using print -s."""
        name, prelude = _model_prelude(model)
        out = resolve_path(output_file)
        out.parent.mkdir(parents=True, exist_ok=True)
        driver = {".png": "-dpng", ".pdf": "-dpdf", ".svg": "-dsvg", ".jpg": "-djpeg", ".eps": "-depsc"}.get(
            out.suffix.lower(), "-dpng")
        code = prelude + f"open_system(mdl); print(['-s' mdl], '{driver}', {matlab_string(str(out))}); file = {matlab_string(str(out))};"
        res = self.run_code(code, capture=["file"], timeout=timeout)
        res.artifacts["diagram"] = str(out)
        return res


simulink_adapter = SimulinkAdapter()
