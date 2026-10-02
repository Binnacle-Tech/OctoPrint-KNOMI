"""Layer map of a G-code file: where each layer starts (byte offset) and its Z.

OctoPrint's G-code viewer follows a print the same way: it maps the file
position OctoPrint reports ("filepos") onto the parsed file. This works for any
slicer and doesn't need OctoPrint's currentZ.

Layer starts come from slicer comments when there are any (PrusaSlicer, Orca,
Bambu Studio, SuperSlicer: ";LAYER_CHANGE" / ";Z:"; Cura: ";LAYER:n"), and
otherwise from Z: a move to a new, higher Z that is followed by extrusion
(so Z-hops and travel lifts don't count).
"""
import bisect
import re

_NUM = r"(-?\d*\.?\d+)"
_Z = re.compile(r"\bZ" + _NUM, re.I)
_E = re.compile(r"\bE" + _NUM, re.I)
_XY = re.compile(r"\b[XY]-?\d", re.I)
_ZCOMMENT = re.compile(r";\s*Z\s*[:=]\s*" + _NUM, re.I)
_TOTAL = [re.compile(r";\s*LAYER_COUNT\s*:\s*(\d+)", re.I),                # Cura
          re.compile(r";\s*total layer (?:number|count)\s*:\s*(\d+)", re.I),  # Orca / Bambu
          re.compile(r";\s*layer_count\s*=\s*(\d+)", re.I)]


# the filament, from the slicer's settings block (PrusaSlicer, Orca, Bambu Studio, SuperSlicer:
# "; filament_type = PETG", Cura: ";MATERIAL:" style names) or the preset name
_MATERIAL = [re.compile(r";\s*filament_type\s*=\s*([^;\n]+)", re.I),
             re.compile(r";\s*filament_settings_id\s*=\s*([^\n]+)", re.I),
             re.compile(r";\s*MATERIAL(?:\.NAME)?\s*[:=]\s*([^\n]+)", re.I)]
MATERIALS = ["PLA", "PETG", "ABS", "ASA", "TPU", "NYLON", "PC"]


def material_of(text):
    """'PETG', 'PLA'... from a slicer string or a file name; None if it doesn't say."""
    t = re.sub(r"[^A-Z0-9]+", " ", (text or "").upper())   # "benchy_petg.gcode" -> "BENCHY PETG GCODE"
    for k, rx in (("PETG", r"PETG|PET G|PCTG"), ("TPU", r"TPU|TPE|FLEX\w*"), ("ASA", r"ASA"), ("ABS", r"ABS"),
                  ("NYLON", r"NYLON|PA|PA6|PA12|PAHT"), ("PC", r"PC|POLYCARBONATE"), ("PLA", r"PLA")):
        if re.search(r"(?<![A-Z])(?:" + rx + r")(?![A-Z])", t):
            return k
    return None


class LayerMap(object):
    def __init__(self, offsets, zs, total=None, material=None):
        self.offsets = offsets      # byte offset where each layer starts, ascending
        self.zs = zs                # Z of each layer, mm
        self.total = total or len(offsets)
        self.material = material    # "PETG" etc, or None

    def at(self, filepos):
        """(layer number starting at 1, Z in mm) at a file position; (0, None) before the first layer."""
        if not self.offsets or filepos is None:
            return 0, None
        i = bisect.bisect_right(self.offsets, filepos) - 1
        if i < 0:
            return 0, None
        return i + 1, self.zs[i]


def scan(path):
    """Build a LayerMap from a G-code file (a few seconds for a big file)."""
    by_comment, cz = [], []          # layer starts from slicer comments
    by_z, zz = [], []                # layer starts from Z moves
    total = None
    material = None
    absolute = True                  # G90 / G91
    e_absolute = True                # M82 / M83
    last_e = 0.0
    z = None                         # current Z
    layer_z = None                   # Z of the last layer found by Z
    cand = None                      # (offset, z) of a Z move not yet confirmed by extrusion
    pending_comment = None           # index into by_comment waiting for its Z
    offset = 0
    with open(path, "rb") as f:
        for raw in f:
            line_start = offset
            try:
                offset += len(raw) if raw.isascii() else len(raw.decode("utf-8").encode("utf-8"))
            except UnicodeDecodeError:
                # OctoPrint reads with errors="replace" and counts the re-encoded length, so each bad
                # byte counts as 3; count it the same way or the layer runs ahead of the print
                offset += len(raw.decode("utf-8", errors="replace").encode("utf-8"))
            line = raw.decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            if line[0] == ";":
                u = line.upper()
                if u.startswith(";LAYER_CHANGE") or u.startswith(";LAYER:"):
                    by_comment.append(line_start)
                    cz.append(z)
                    pending_comment = len(by_comment) - 1
                    continue
                m = _ZCOMMENT.match(line)
                if m and pending_comment is not None:
                    cz[pending_comment] = float(m.group(1))
                    pending_comment = None
                    continue
                if material is None:
                    for rx in _MATERIAL:
                        m = rx.match(line)
                        if m:
                            material = material_of(m.group(1))
                            break
                if total is None:
                    for rx in _TOTAL:
                        m = rx.search(line)
                        if m:
                            total = int(m.group(1))
                            break
                continue
            code = line.split(";", 1)[0].strip()
            if not code:
                continue
            word = code.split(None, 1)[0].upper()
            if word == "G90":
                absolute = True
            elif word == "G91":
                absolute = False
            elif word == "M82":
                e_absolute = True
            elif word == "M83":
                e_absolute = False
            elif word == "G92":
                m = _E.search(code)
                if m:
                    last_e = float(m.group(1))
                m = _Z.search(code)
                if m:
                    z = float(m.group(1))
            elif word in ("G0", "G1", "G00", "G01"):
                m = _Z.search(code)
                if m:
                    v = float(m.group(1))
                    z = v if absolute or z is None else z + v
                    if pending_comment is not None and cz[pending_comment] is None:
                        cz[pending_comment] = z
                        pending_comment = None
                    if layer_z is None or z > layer_z + 1e-4:
                        cand = (line_start, z)
                    else:
                        cand = None
                if len(by_comment) >= 2:
                    continue   # the slicer marks its layers: the extrusion check below is only for files that don't
                m = _E.search(code)
                if m:
                    e = float(m.group(1))
                    extruding = (e > last_e) if e_absolute else (e > 0)
                    if e_absolute:
                        last_e = e
                    if extruding and _XY.search(code) and cand and abs(cand[1] - z) < 1e-4:
                        by_z.append(cand[0])
                        zz.append(cand[1])
                        layer_z = cand[1]
                        cand = None
    if len(by_comment) >= 2:
        # fill any comment layer whose Z never showed up from the one before it
        for i, v in enumerate(cz):
            if v is None:
                cz[i] = cz[i - 1] if i else 0.0
        return LayerMap(by_comment, cz, total, material or material_of(path.rsplit("/", 1)[-1]))
    return LayerMap(by_z, zz, total, material or material_of(path.rsplit("/", 1)[-1]))
