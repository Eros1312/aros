"""Small, deterministic organizer meshes. No scripts, model calls or native CAD.

The solid is the boundary of a non-overlapping rectilinear cell complex. Every
face uses the same coordinate subdivisions, including the base: no T-junctions,
overlapping boxes or internal faces are exported. Verify the serialized float32
STL, not just the in-memory source, before calling a job verified.
"""
from collections import Counter, defaultdict
import hashlib
import math
import struct
import time

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_TRIANGLES = 4096
MAX_STL_BYTES = 84 + MAX_TRIANGLES * 50
GENERATOR_VERSION = 'aros-organizer-1'


class OrganizerParameters(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    width_mm: float = Field(default=120, ge=20, le=180)
    depth_mm: float = Field(default=80, ge=20, le=180)
    height_mm: float = Field(default=35, ge=10, le=100)
    wall_mm: float = Field(default=2, ge=1.2, le=6)
    base_mm: float = Field(default=2, ge=1.2, le=8)
    columns: int = Field(default=2, ge=1, le=6)
    rows: int = Field(default=1, ge=1, le=6)

    @model_validator(mode='after')
    def useful_compartments(self):
        if self.height_mm - self.base_mm < 5:
            raise ValueError('La profundidad interior debe ser al menos 5 mm.')
        for length, count in ((self.width_mm, self.columns), (self.depth_mm, self.rows)):
            if (length - (count + 1) * self.wall_mm) / count < 5:
                raise ValueError('Cada compartimento debe tener al menos 5 mm de ancho y fondo.')
        return self


def cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0])


def normal(a, b, c):
    return cross(tuple(b[i]-a[i] for i in range(3)), tuple(c[i]-a[i] for i in range(3)))


def _budget(deadline):
    if time.monotonic() > deadline:
        raise ValueError('La geometría superó el tiempo máximo de cálculo.')


def _axis(length, count, wall):
    gap = (length - (count + 1) * wall) / count
    coords = [0.0, float(wall)]
    for index in range(count):
        coords.extend([wall + (index + 1) * gap + index * wall,
                       (index + 1) * (gap + wall) + wall])
    coords[-1] = float(length)
    # Round once to the actual STL representation; every shared point is exact.
    return [struct.unpack('<f', struct.pack('<f', value))[0] for value in coords]


def generate_organizer(parameters):
    p = OrganizerParameters.model_validate(parameters)
    deadline = time.monotonic() + 3
    xs, ys = _axis(p.width_mm, p.columns, p.wall_mm), _axis(p.depth_mm, p.rows, p.wall_mm)
    zs = [0.0, p.base_mm, p.height_mm]
    occupied = {(x, y, z) for x in range(len(xs)-1) for y in range(len(ys)-1)
                for z in range(2) if z == 0 or x % 2 == 0 or y % 2 == 0}
    triangles = []
    for x, y, z in sorted(occupied):
        _budget(deadline)
        a, b, c, d, e, f = xs[x], xs[x+1], ys[y], ys[y+1], zs[z], zs[z+1]
        faces = [((-1,0,0), [(a,c,e),(a,c,f),(a,d,f),(a,d,e)]),
                 ((1,0,0), [(b,c,e),(b,d,e),(b,d,f),(b,c,f)]),
                 ((0,-1,0), [(a,c,e),(b,c,e),(b,c,f),(a,c,f)]),
                 ((0,1,0), [(a,d,e),(a,d,f),(b,d,f),(b,d,e)]),
                 ((0,0,-1), [(a,c,e),(a,d,e),(b,d,e),(b,c,e)]),
                 ((0,0,1), [(a,c,f),(b,c,f),(b,d,f),(a,d,f)])]
        for (dx,dy,dz), quad in faces:
            if (x+dx,y+dy,z+dz) not in occupied:
                triangles.extend([(quad[0],quad[1],quad[2]), (quad[0],quad[2],quad[3])])
    if len(triangles) > MAX_TRIANGLES:
        raise ValueError('La geometría supera el límite de triángulos.')
    raw = bytearray(b'Aros parametric organizer; dimensions interpreted in millimetres'.ljust(80,b' '))
    raw.extend(struct.pack('<I', len(triangles)))
    for a, b, c in triangles:
        n = normal(a,b,c); magnitude = math.sqrt(sum(value*value for value in n))
        raw.extend(struct.pack('<12fH', *(value/magnitude for value in n), *a, *b, *c, 0))
    blob = bytes(raw)
    return blob, verify_stl(blob, p, deadline=deadline)


def verify_stl(blob, parameters, *, deadline=None):
    """Check bounded serialized geometry, edge/vertex manifoldness and volume."""
    deadline = deadline or time.monotonic() + 3
    p = OrganizerParameters.model_validate(parameters)
    if not isinstance(blob, bytes) or not 134 <= len(blob) <= MAX_STL_BYTES:
        raise ValueError('Tamaño STL inválido.')
    count = struct.unpack_from('<I', blob, 80)[0]
    if not 0 < count <= MAX_TRIANGLES or len(blob) != 84 + count * 50:
        raise ValueError('Estructura STL inválida.')
    edges = Counter(); directed = Counter(); points = set(); links = defaultdict(list)
    triangle_keys = set(); volume = 0.0
    for index in range(count):
        _budget(deadline)
        values = struct.unpack_from('<12fH', blob, 84+index*50)
        if not all(math.isfinite(value) for value in values[:12]) or values[-1] != 0:
            raise ValueError('Valores STL inválidos.')
        a,b,c = tuple(values[3:6]),tuple(values[6:9]),tuple(values[9:12])
        n=normal(a,b,c); length=math.sqrt(sum(v*v for v in n))
        if length <= 1e-8 or any(abs(values[i]-n[i]/length)>1e-5 for i in range(3)):
            raise ValueError('Triángulo degenerado u orientación inválida.')
        key=tuple(sorted((a,b,c)))
        if key in triangle_keys:raise ValueError('Triángulo duplicado.')
        triangle_keys.add(key);points.update((a,b,c))
        for u,v,w in ((a,b,c),(b,c,a),(c,a,b)):
            edges[tuple(sorted((u,v)))]+=1;directed[(u,v)]+=1;links[u].append((v,w))
        volume += sum(a[i]*cross(b,c)[i] for i in range(3))/6
    if any(n != 2 for n in edges.values()) or any(directed[(a,b)] != 1 or directed[(b,a)] != 1 for a,b in edges):
        raise ValueError('La malla tiene bordes abiertos u orientación inconsistente.')
    # Each vertex link must be one cycle, excluding pinched/non-manifold vertices.
    adjacency=defaultdict(set)
    for a,b in edges:adjacency[a].add(b);adjacency[b].add(a)
    def connected(graph):
        start=next(iter(graph));seen={start};pending=[start]
        while pending:
            _budget(deadline)
            for adjacent in graph[pending.pop()]:
                if adjacent not in seen:seen.add(adjacent);pending.append(adjacent)
        return len(seen)==len(graph)
    if not connected(adjacency):raise ValueError('La malla contiene piezas separadas.')
    for pairs in links.values():
        graph=defaultdict(set)
        for a,b in pairs:graph[a].add(b);graph[b].add(a)
        if any(len(v)!=2 for v in graph.values()) or not connected(graph):
            raise ValueError('La malla tiene un vértice no manifold.')
    bounds=[[min(point[i] for point in points),max(point[i] for point in points)] for i in range(3)]
    dimensions=[p.width_mm,p.depth_mm,p.height_mm]
    if any(abs(bounds[i][0])>1e-6 or abs(bounds[i][1]-dimensions[i])>1e-4 for i in range(3)):
        raise ValueError('Las dimensiones STL no coinciden con el encargo.')
    expected=p.width_mm*p.depth_mm*p.height_mm-(p.width_mm-(p.columns+1)*p.wall_mm)*(p.depth_mm-(p.rows+1)*p.wall_mm)*(p.height_mm-p.base_mm)
    if volume<=0 or not math.isclose(volume,expected,rel_tol=1e-5,abs_tol=1e-3):
        raise ValueError('El volumen STL no coincide con el organizador.')
    if len(points)-len(edges)+count != 2:
        raise ValueError('Topología inesperada para el organizador.')
    return {'sha256':hashlib.sha256(blob).hexdigest(),'bytes':len(blob),'triangles':count,
            'vertices':len(points),'edges':len(edges),'volume_mm3':round(volume,4),
            'dimensions_mm':[round(b[1]-b[0],5) for b in bounds], 'units':'mm',
            'watertight':True,'vertex_manifold':True,'consistent_winding':True,'components':1,
            'generator':GENERATOR_VERSION,'validation':'serialized_stl_geometry',
            'physical_print_verified':False}
