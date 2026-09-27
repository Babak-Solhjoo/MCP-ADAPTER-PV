"""Java programs that the COMSOL adapter compiles and runs with comsolbatch to inspect and evaluate models.

They use only the COMSOL Java API that ships with every installation, so no Python bridge (MPh/JPype) is
needed. Each program prints one JSON object between the adapter's JSON markers (see base.JSON_START).
"""
from __future__ import annotations

import json

from .base import JSON_END, JSON_START

_HELPERS = r"""
  static String q(String s) {
    if (s == null) return "null";
    StringBuilder b = new StringBuilder("\"");
    for (char ch : s.toCharArray()) {
      if (ch == '"') b.append("\\\"");
      else if (ch == '\\') b.append("\\\\");
      else if (ch == '\n') b.append("\\n");
      else if (ch == '\t') b.append("\\t");
      else if (ch < 0x20) b.append(' ');
      else b.append(ch);
    }
    return b.append('"').toString();
  }
  static String arr(String[] a) {
    if (a == null) return "[]";
    StringBuilder b = new StringBuilder("[");
    for (int i = 0; i < a.length; i++) { if (i > 0) b.append(','); b.append(q(a[i])); }
    return b.append(']').toString();
  }
  static String num(double v) {
    return (Double.isNaN(v) || Double.isInfinite(v)) ? "null" : Double.toString(v);
  }
  static String mat(double[][] v) {
    if (v == null) return "null";
    StringBuilder b = new StringBuilder("[");
    for (int i = 0; i < v.length; i++) {
      if (i > 0) b.append(',');
      b.append('[');
      for (int j = 0; j < v[i].length; j++) { if (j > 0) b.append(','); b.append(num(v[i][j])); }
      b.append(']');
    }
    return b.append(']').toString();
  }
"""

SUMMARY_TEMPLATE = r"""
import com.comsol.model.*;
import com.comsol.model.util.*;

public class %CLASS% {
%HELPERS%
  public static Model run() {
    Model model;
    try { model = ModelUtil.load("McpInspect", %PATH%); }
    catch (Exception ex) { System.out.println("%JSON_START%{\"error\":" + q(String.valueOf(ex.getMessage())) + "}%JSON_END%"); return null; }
    try {
    StringBuilder o = new StringBuilder("{");
    o.append("\"title\":").append(q(model.title())).append(',');
    StringBuilder ps = new StringBuilder("[");
    String[] pn = model.param().varnames();
    for (int i = 0; i < pn.length; i++) {
      if (i > 0) ps.append(',');
      String d = "";
      try { d = model.param().descr(pn[i]); } catch (Exception ex) { }
      ps.append("{\"name\":").append(q(pn[i])).append(",\"expression\":").append(q(model.param().get(pn[i])))
        .append(",\"description\":").append(q(d)).append('}');
    }
    o.append("\"parameters\":").append(ps).append("],");
    StringBuilder cs = new StringBuilder("[");
    String[] comps = model.component().tags();
    for (int c = 0; c < comps.length; c++) {
      if (c > 0) cs.append(',');
      ModelNode comp = model.component(comps[c]);
      cs.append("{\"tag\":").append(q(comps[c])).append(",\"physics\":[");
      String[] ph = comp.physics().tags();
      for (int i = 0; i < ph.length; i++) {
        if (i > 0) cs.append(',');
        String type = "";
        try { type = comp.physics(ph[i]).getType(); } catch (Exception ex) { }
        cs.append("{\"tag\":").append(q(ph[i])).append(",\"type\":").append(q(type))
          .append(",\"label\":").append(q(comp.physics(ph[i]).label())).append('}');
      }
      cs.append("],\"multiphysics\":[");
      String[] mp = comp.multiphysics().tags();
      for (int i = 0; i < mp.length; i++) {
        if (i > 0) cs.append(',');
        String type = "";
        try { type = comp.multiphysics(mp[i]).getType(); } catch (Exception ex) { }
        cs.append("{\"tag\":").append(q(mp[i])).append(",\"type\":").append(q(type)).append('}');
      }
      cs.append("],\"materials\":").append(arr(comp.material().tags())).append('}');
    }
    o.append("\"components\":").append(cs).append("],");
    StringBuilder ss = new StringBuilder("[");
    String[] st = model.study().tags();
    for (int i = 0; i < st.length; i++) {
      if (i > 0) ss.append(',');
      ss.append("{\"tag\":").append(q(st[i])).append(",\"label\":").append(q(model.study(st[i]).label())).append(",\"steps\":[");
      String[] steps = model.study(st[i]).feature().tags();
      for (int j = 0; j < steps.length; j++) {
        if (j > 0) ss.append(',');
        String type = "";
        try { type = model.study(st[i]).feature(steps[j]).getType(); } catch (Exception ex) { }
        ss.append("{\"tag\":").append(q(steps[j])).append(",\"type\":").append(q(type)).append('}');
      }
      ss.append("]}");
    }
    o.append("\"studies\":").append(ss).append("],");
    o.append("\"datasets\":").append(arr(model.result().dataset().tags())).append(',');
    o.append("\"plot_groups\":").append(arr(model.result().tags())).append(',');
    StringBuilder ns = new StringBuilder("[");
    String[] nt = model.result().numerical().tags();
    for (int i = 0; i < nt.length && i < %MAXNUM%; i++) {
      if (i > 0) ns.append(',');
      NumericalFeature nf = model.result().numerical(nt[i]);
      ns.append("{\"tag\":").append(q(nt[i])).append(",\"label\":").append(q(nf.label()));
      try { ns.append(",\"type\":").append(q(nf.getType())); } catch (Exception ex) { }
      try { ns.append(",\"expressions\":").append(arr(nf.getStringArray("expr"))); } catch (Exception ex) { }
      try { ns.append(",\"units\":").append(arr(nf.getStringArray("unit"))); } catch (Exception ex) { }
      if (%EVALUATE%) {
        try { String vals = mat(nf.getReal()); ns.append(",\"values\":").append(vals); }
        catch (Exception ex) { ns.append(",\"error\":").append(q(ex.getMessage())); }
      }
      ns.append('}');
    }
    o.append("\"derived_values\":").append(ns).append("]}");
    System.out.println("%JSON_START%" + o + "%JSON_END%");
    } catch (Exception ex) {
      System.out.println("%JSON_START%{\"error\":" + q(String.valueOf(ex.getMessage())) + "}%JSON_END%");
    }
    try { ModelUtil.remove("McpInspect"); } catch (Exception ex) { }
    return null;
  }

  public static void main(String[] args) { run(); }
}
"""

EVALUATE_TEMPLATE = r"""
import com.comsol.model.*;
import com.comsol.model.util.*;

public class %CLASS% {
%HELPERS%
  public static Model run() {
    Model model;
    try { model = ModelUtil.load("McpEval", %PATH%); }
    catch (Exception ex) { System.out.println("%JSON_START%{\"error\":" + q(String.valueOf(ex.getMessage())) + "}%JSON_END%"); return null; }
    try {
    StringBuilder o = new StringBuilder("{\"results\":[");
    String[] exprs = %EXPRS%;
    String[] units = %UNITS%;
    String type = %TYPE%;
    String dataset = %DATASET%;
    int[] entities = %ENTITIES%;
    for (int i = 0; i < exprs.length; i++) {
      if (i > 0) o.append(',');
      String tag = "mcpeval" + i;
      o.append("{\"expression\":").append(q(exprs[i]));
      try {
        NumericalFeature nf = model.result().numerical().create(tag, type);
        if (dataset.length() > 0) nf.set("data", dataset);
        nf.set("expr", new String[] { exprs[i] });
        if (units[i].length() > 0) nf.set("unit", new String[] { units[i] });
        String vals;
        if (type.equals("EvalGlobal")) {
          vals = mat(nf.getReal());
        } else if (entities.length > 0) {
          nf.selection().set(entities);
          vals = mat(nf.getReal());
        } else {
          nf.selection().all();
          try {
            vals = mat(nf.getReal());
          } catch (Exception ex) {
            if (!String.valueOf(ex.getMessage()).contains("not_meshed")) throw ex;
            // some entities are excluded from the mesh: keep only the meshed ones
            int[] all = nf.selection().entities();
            java.util.ArrayList<Integer> keep = new java.util.ArrayList<Integer>();
            NumericalFeature probe = model.result().numerical().create(tag + "p", type);
            if (dataset.length() > 0) probe.set("data", dataset);
            probe.set("expr", new String[] { "1" });
            for (int e : all) {
              try { probe.selection().set(new int[] { e }); probe.getReal(); keep.add(e); } catch (Exception ignored) { }
            }
            model.result().numerical().remove(tag + "p");
            int[] meshed = new int[keep.size()];
            for (int k = 0; k < meshed.length; k++) meshed[k] = keep.get(k);
            nf.selection().set(meshed);
            vals = mat(nf.getReal());
            o.append(",\"meshed_entities\":").append(java.util.Arrays.toString(meshed));
          }
        }
        o.append(",\"type\":").append(q(type)).append(",\"values\":").append(vals);
        try { o.append(",\"unit\":").append(arr(nf.getStringArray("unit"))); } catch (Exception ex) { }
        model.result().numerical().remove(tag);
      } catch (Exception ex) {
        o.append(",\"error\":").append(q(ex.getMessage()));
      }
      o.append('}');
    }
    o.append("]}");
    System.out.println("%JSON_START%" + o + "%JSON_END%");
    } catch (Exception ex) {
      System.out.println("%JSON_START%{\"error\":" + q(String.valueOf(ex.getMessage())) + "}%JSON_END%");
    }
    try { ModelUtil.remove("McpEval"); } catch (Exception ex) { }
    return null;
  }

  public static void main(String[] args) { run(); }
}
"""


def _jstr(s: str) -> str:
    """Java string literal (JSON escaping is a valid subset for the characters we pass)."""
    return json.dumps(str(s))


def _jarr(items: list[str]) -> str:
    return "new String[] {" + ", ".join(_jstr(x) for x in items) + "}"


def _fill(template: str, class_name: str, **values: str) -> str:
    out = (template.replace("%HELPERS%", _HELPERS).replace("%CLASS%", class_name)
           .replace("%JSON_START%", JSON_START).replace("%JSON_END%", JSON_END))
    for key, val in values.items():
        out = out.replace(f"%{key}%", val)
    return out


def summary_java(class_name: str, model_path: str, evaluate: bool = True, max_numerical: int = 40) -> str:
    return _fill(SUMMARY_TEMPLATE, class_name, PATH=_jstr(model_path.replace("\\", "/")),
                 EVALUATE="true" if evaluate else "false", MAXNUM=str(int(max_numerical)))


EVAL_TYPES = {"global": "EvalGlobal", "volume_integral": "IntVolume", "surface_integral": "IntSurface",
              "line_integral": "IntLine", "volume_average": "AvVolume", "surface_average": "AvSurface",
              "volume_maximum": "MaxVolume", "volume_minimum": "MinVolume", "surface_maximum": "MaxSurface",
              "surface_minimum": "MinSurface"}


def evaluate_java(class_name: str, model_path: str, expressions: list[str], units: list[str] | None = None,
                  kind: str = "global", dataset: str = "", entities: list[int] | None = None) -> str:
    eval_type = EVAL_TYPES.get(kind, kind)
    units = list(units or [])
    units += [""] * (len(expressions) - len(units))
    return _fill(EVALUATE_TEMPLATE, class_name, PATH=_jstr(model_path.replace("\\", "/")),
                 EXPRS=_jarr(expressions), UNITS=_jarr(units[: len(expressions)]), TYPE=_jstr(eval_type),
                 DATASET=_jstr(dataset or ""),
                 ENTITIES="new int[] {" + ", ".join(str(int(e)) for e in (entities or [])) + "}")
